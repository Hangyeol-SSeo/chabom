"""KB차차차(kbchachacha.com) 어댑터.

**2026-09-17 재조사로 정정된 사실** (crawler/compliance.py 참고): 최초 조사에서 "매물 상세페이지가
robots.txt로 차단됨"이라고 판단한 건 오판이었다. robots.txt의 `Disallow: /public/review/car/detail.kbc`는
리뷰 작성 페이지이고, 실제 매물 상세페이지는 `/public/car/detail.kbc?carSeq=...`로 전혀 다른 경로이며
Disallow 목록에 없다. 목록도 `GET /public/search/list.empty?page=N`이 세션/쿠키 없이 실제 매물 HTML을
반환해 requests+bs4만으로 충분하다(Playwright 불필요).

**데이터 품질**: 이 프로젝트가 다루는 세 사이트(보배드림/케이카/KB차차차) 중 가장 풍부하다 —
상세페이지가 정적 HTML로 사고유무·전손이력·침수이력·소유자변경·압류/저당/세금미납까지 노출하고,
차종(body_type)도 별도 필드로 제공한다(보배드림엔 없음). 다만:
- 사고유무(있음/없음)는 부위별 상세가 아닌 포괄적 배지라서 `performance_record.frame_damage` 하드필터에는
  연결하지 않는다 — 대신 `scoring/rules.py::rule_accident_history_flag`류의 리스크 감점으로만 반영한다.
- 소유자변경도 있음/없음 배지만 제공해 정확한 횟수를 알 수 없다 — "있음"은 보수적으로 1회로 근사한다
  (실제로는 더 많을 수 있음, `owner_change_count`가 하한값이라는 점을 감안해 해석할 것).
- 외판교환/프레임손상의 부위별 목록은 제3자 성능점검기관(카모두 carmodoo.com)의 원본 리포트에만 있고
  이 어댑터는 그 리포트를 파싱하지 않는다 — `verification_links`로 원본 링크만 제공한다(사용자가 직접
  새 창에서 확인).

**2026-09-17 개편**: 목록 조회를 "전량 수집 후 로컬 필터링"에서 "검색 조건을 목록 URL
(`/public/search/list.empty`)에 실어 서버가 직접 필터링한 결과만 받아오기"로 바꿨다(실측으로
`sellAmt=min,max`·`km=min,max`·`regiDay=year,year`·`gas=코드` 파라미터가 서버단에서 동작함을
확인 — 3,000~4,000만원으로 필터한 응답의 가격이 전부 그 범위 안임을 확인했다). 전송 계층도
`requests`에서 Playwright(실브라우저)로 바꿨다(사용자 요청 — 개인 용도 도구라 브라우저 기반
수집을 우선한다).
"""
from __future__ import annotations

import logging
import re
from typing import Iterator, Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from crawler.base_adapter import BaseAdapter, SearchParams
from crawler.browser_fetch import BrowserFetcher
from crawler.rate_limiter import RateLimiter
from normalizer.schema import (
    Dealer,
    InsuranceHistory,
    Listing,
    ListingText,
    PerformanceRecord,
    Photos,
    UsageHistory,
    Vehicle,
    VerificationLink,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.kbchachacha.com"
CARHISTORY_URL = "https://www.carhistory.or.kr/main.car"

_FUEL_MAP = {
    "가솔린": "gasoline",
    "휘발유": "gasoline",
    "디젤": "diesel",
    "경유": "diesel",
    "LPG": "lpg",
    "하이브리드": "hybrid",
    "전기": "ev",
    "수소": "ev",
}

# 정규화된 fuel_type/transmission 값 -> KB차차차 목록 검색 파라미터 코드(실측 확인, 모듈 docstring 참고).
_FUEL_TO_PARAM = {
    "gasoline": "004001",
    "diesel": "004002",
    "lpg": "004003",
    "hybrid": "004005",  # 하이브리드(가솔린). LPG/디젤 하이브리드는 목록에서 놓칠 수 있음.
    "ev": "004007",
}
_TRANSMISSION_TO_PARAM = {"automatic": "005002", "manual": "005001"}


def _to_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


class KbchachachaAdapter(BaseAdapter):
    source = "kbchachacha"

    def __init__(
        self,
        fetcher: Optional[BrowserFetcher] = None,
        rate_limiter: Optional[RateLimiter] = None,
        request_timeout_sec: float = 15.0,
    ):
        self.fetcher = fetcher or BrowserFetcher()
        # 실측 중 확인: 1~3초 기본 딜레이로 여러 페이지를 연속 수집하면 상당수 요청이 "로봇여부 확인"
        # 봇 차단 페이지로 되돌아왔다(parse_detail의 감지 로직 참고). 우회 대신 요청 자체를 줄이는
        # 방향으로 대응한다 — 기본 딜레이를 다른 어댑터보다 넉넉하게 잡는다.
        self.rate_limiter = rate_limiter or RateLimiter(min_delay_sec=2.0, max_delay_sec=4.5)
        self.request_timeout_sec = request_timeout_sec

    def _get_html(self, url: str) -> Optional[str]:
        self.rate_limiter.wait()
        html = self.fetcher.get_html(url, timeout_sec=self.request_timeout_sec)
        if html is None:
            logger.warning("요청 실패: %s", url)
        return html

    def _build_list_url(self, params: SearchParams, page: int) -> str:
        """검색 조건을 실제 목록 URL 파라미터로 실어 서버단 필터링을 받는다(모듈 docstring 참고)."""
        query: dict[str, str] = {"page": str(page)}
        if params.min_price_krw is not None or params.max_price_krw is not None:
            min_p = params.min_price_krw // 10_000 if params.min_price_krw else ""
            max_p = params.max_price_krw // 10_000 if params.max_price_krw else ""
            query["sellAmt"] = f"{min_p},{max_p}"
        if params.mileage_min_km is not None or params.mileage_max_km is not None:
            query["km"] = f"{params.mileage_min_km or ''},{params.mileage_max_km or ''}"
        if params.year_min is not None or params.year_max is not None:
            query["regiDay"] = f"{params.year_min or ''},{params.year_max or ''}"
        if params.fuel_type and params.fuel_type in _FUEL_TO_PARAM:
            query["gas"] = _FUEL_TO_PARAM[params.fuel_type]
        if params.transmission and params.transmission in _TRANSMISSION_TO_PARAM:
            query["autoGbn"] = _TRANSMISSION_TO_PARAM[params.transmission]
        return f"{BASE_URL}/public/search/list.empty?{urlencode(query)}"

    def fetch_listing_urls(self, params: SearchParams) -> Iterator[str]:
        seen: set[str] = set()
        for page in range(1, params.max_pages + 1):
            list_url = self._build_list_url(params, page)
            html = self._get_html(list_url)
            if html is None:
                continue
            soup = BeautifulSoup(html, "html.parser")
            links = soup.select('a[href*="detail.kbc"]')
            page_ids: set[str] = set()
            for a in links:
                href = a.get("href", "")
                m = re.search(r"carSeq=(\d+)", href)
                if not m:
                    continue
                car_seq = m.group(1)
                if car_seq in seen:
                    continue
                seen.add(car_seq)
                page_ids.add(car_seq)
                yield f"{BASE_URL}/public/car/detail.kbc?carSeq={car_seq}"
            if not page_ids:
                break

    def parse_detail(self, url: str) -> Listing:
        m = re.search(r"carSeq=(\d+)", url)
        listing_id = m.group(1) if m else url
        html = self._get_html(url)
        if html is None:
            raise RuntimeError(f"상세페이지를 가져오지 못했습니다: {url}")
        soup = BeautifulSoup(html, "html.parser")

        vehicle = self._parse_vehicle(soup)

        # 실측 중 발견: 연속 요청이 쌓이면 KB차차차가 실제 매물 대신 "로봇여부 확인" 봇 차단
        # 페이지를 돌려주는 경우가 있었다(<title>이 "로봇여부 확인"이 됨). 이 프로젝트는 봇 차단을
        # 우회하지 않는다 — 대신 이런 응답을 감지해 건너뛴다(호출부에서 RuntimeError로 처리).
        if self._looks_like_bot_block(vehicle):
            raise RuntimeError(
                f"실제 매물 데이터가 아닌 응답(봇 차단 페이지로 추정)이라 건너뜁니다: {url}"
            )

        insurance_history = self._parse_insurance_summary(soup)
        listing_text = self._parse_listing_text(soup)
        photos = self._parse_photos(soup)
        verification_links = self._build_verification_links(soup, url)
        dealer = self._parse_dealer(soup, vehicle)

        return Listing(
            listing_id=listing_id,
            source=self.source,
            url=url,
            vehicle=vehicle,
            insurance_history=insurance_history,
            performance_record=PerformanceRecord(),  # 부위별 데이터는 카모두 원본 리포트에만 있음(위 docstring)
            dealer=dealer,
            listing_text=listing_text,
            photos=photos,
            tire_brand=None,
            verification_links=verification_links,
        )

    def fetch_normalized_listings(self, params: SearchParams):
        for url in self.fetch_listing_urls(params):
            try:
                listing = self.parse_detail(url)
            except RuntimeError as exc:
                logger.warning("상세페이지 파싱 실패, 건너뜀: %s", exc)
                continue
            if self._passes_filters(listing, params):
                yield listing

    def _passes_filters(self, listing: Listing, params: SearchParams) -> bool:
        price = listing.vehicle.price_krw
        if params.min_price_krw is not None and (price is None or price < params.min_price_krw):
            return False
        if params.max_price_krw is not None and (price is None or price > params.max_price_krw):
            return False
        if params.transmission and listing.vehicle.transmission and listing.vehicle.transmission != params.transmission:
            return False
        if params.fuel_type and listing.vehicle.fuel_type and listing.vehicle.fuel_type != params.fuel_type:
            return False
        return True

    # -- 개별 섹션 파서 -----------------------------------------------------

    def _looks_like_bot_block(self, vehicle: Vehicle) -> bool:
        """봇 차단 페이지("로봇여부 확인")를 실제 매물로 착각하지 않도록 가드."""
        if vehicle.make == "로봇여부":
            return True
        return vehicle.price_krw is None and vehicle.model_year is None and vehicle.mileage_km is None

    def _spec_table(self, soup: BeautifulSoup) -> dict[str, str]:
        table = soup.select_one("table")
        result: dict[str, str] = {}
        if not table:
            return result
        for th in table.select("th"):
            td = th.find_next_sibling("td")
            if td:
                result[th.get_text(strip=True)] = td.get_text(strip=True)
        return result

    def _find_dt_value(self, soup: BeautifulSoup, label: str) -> Optional[str]:
        node = soup.find(string=re.compile(re.escape(label)))
        if not node:
            return None
        dt = node.find_parent("dt")
        if not dt:
            return None
        dd = dt.find_next_sibling("dd")
        return dd.get_text(strip=True) if dd else None

    def _parse_dealer(self, soup: BeautifulSoup, vehicle: Vehicle) -> Dealer:
        """딜러 블랙리스트(storage/dealers.py)의 1차 매칭 키로 쓸 dealerNo를 추출한다.

        페이지에 프로필 링크로 노출되지 않고, "전화 연결" 레이어를 여는 JS 설정 객체
        (`{ name: 'dealerNo', value: '000000' }` 형태)에만 박혀 있다는 걸 실측으로 확인했다.
        전화번호는 클릭해야 드러나는 번호 연결 시스템이라 크롤링으로 확보하지 않는다 —
        사용자가 검증 화면에서 직접 입력해야 교차 사이트 매칭에 쓸 수 있다.
        """
        html = str(soup)
        m = re.search(r"name:\s*'dealerNo',\s*value:\s*'(\d+)'", html)
        dealer_id = m.group(1) if m else ""
        return Dealer(dealer_id=dealer_id, region=vehicle.region)

    def _find_tri_state(self, soup: BeautifulSoup, label: str) -> Optional[bool]:
        """KB차차차는 전손/침수 등을 "있음"/"없음"으로 명시하는 경우가 많다 — 체크리스트가
        "확인 안 됨"과 "확인해서 없음"을 구분할 수 있도록 삼상태로 반환한다(체크리스트 검증
        기능, 2026-09-18). 필드 자체를 못 찾으면 None(확인 안 됨), "있음"이면 True, "없음"이면 False.
        """
        text = self._find_dt_value(soup, label)
        if text is None:
            return None
        if "있음" in text:
            return True
        if "없음" in text:
            return False
        return None

    def _parse_vehicle(self, soup: BeautifulSoup) -> Vehicle:
        spec = self._spec_table(soup)

        make = ""
        model = ""
        region = ""
        title_tag = soup.select_one("title")
        if title_tag:
            title_text = title_tag.get_text(strip=True)
            head = title_text.split("|")[0].strip()  # "BMW 3시리즈 (G20) · 가솔린 · 경기"
            parts = [p.strip() for p in head.split("·")]
            if parts:
                name_tokens = parts[0].split()
                make = name_tokens[0] if name_tokens else ""
                model = " ".join(name_tokens[1:]) if len(name_tokens) > 1 else ""
            if len(parts) >= 3:
                region = parts[-1]

        model_year = None
        first_registration_date = None
        year_text = spec.get("연식", "")
        ym = re.match(r"(\d{2})년(\d{2})월", year_text)
        if ym:
            model_year = 2000 + int(ym.group(1))
            first_registration_date = f"{model_year}-{ym.group(2)}-01"  # 일자는 월 단위 근사치

        mileage_km = _to_int(spec.get("주행거리"))

        transmission_raw = spec.get("변속기", "")
        transmission = (
            "automatic" if any(k in transmission_raw for k in ("자동", "오토"))
            else ("manual" if any(k in transmission_raw for k in ("수동", "매뉴얼")) else "")
        )

        fuel_raw = spec.get("연료", "")
        fuel_type = _FUEL_MAP.get(fuel_raw, fuel_raw)

        body_type = spec.get("차종", "")

        price_krw = None
        price_label = soup.find(string=re.compile("판매가격"))
        if price_label:
            dt = price_label.find_parent("dt")
            dd = dt.find_next_sibling("dd") if dt else None
            if dd:
                price_krw = _to_int(dd.get_text())
                if price_krw is not None:
                    price_krw *= 10_000  # 사이트 표기 단위: 만원

        return Vehicle(
            make=make,
            model=model,
            trim="",  # 사이트가 트림을 별도 필드로 노출하지 않아 모델명에 통합됨(보배드림과 동일한 단순화)
            model_year=model_year,
            first_registration_date=first_registration_date,
            mileage_km=mileage_km,
            transmission=transmission,
            fuel_type=fuel_type,
            body_type=body_type,
            price_krw=price_krw,
            new_car_price_krw=None,
            region=region,
        )

    def _parse_insurance_summary(self, soup: BeautifulSoup) -> InsuranceHistory:
        full_text = soup.get_text(" ", strip=True)

        owner_change_count = 0
        owner_change_text = self._find_dt_value(soup, "소유자변경")
        if owner_change_text and "있음" in owner_change_text:
            # 사이트가 있음/없음만 노출하고 정확한 횟수는 안 줌 — 하한값으로 1회 근사(위 docstring 참고).
            owner_change_count = 1

        history_disclosed = True if "보험사고정보" in full_text else None

        return InsuranceHistory(
            usage_history=UsageHistory(),  # 있음/없음만 노출되어 렌트/택시/영업용 구분 불가 — 과대추정 방지 위해 비움
            owner_change_count=owner_change_count,
            owner_change_log=[],
            own_damage_claims=[],
            other_party_damage_claims=[],
            info_unavailable_periods=[],
            history_disclosed=history_disclosed,
            flood_damage=self._find_tri_state(soup, "침수이력"),
            total_loss=self._find_tri_state(soup, "전손이력"),
        )

    def _parse_listing_text(self, soup: BeautifulSoup) -> ListingText:
        full_text = soup.get_text(" ", strip=True)
        tags: list[str] = []

        accident_match = re.search(r"사고(있음|없음)", full_text)
        if accident_match:
            tags.append(f"사고{accident_match.group(1)}")

        total_loss = self._find_dt_value(soup, "전손이력")
        if total_loss and "있음" in total_loss:
            tags.append("전손이력있음")

        flood = self._find_dt_value(soup, "침수이력")
        if flood and "있음" in flood:
            tags.append("침수이력있음")

        usage_history = self._find_dt_value(soup, "용도이력")
        if usage_history and "있음" in usage_history:
            tags.append("용도이력있음")

        for warn_label in ("압류", "저당", "세금미납"):
            spec = self._spec_table(soup)
            if spec.get(warn_label) and spec.get(warn_label) != "없음":
                tags.append(f"{warn_label}있음")

        return ListingText(description_raw="", claims_parsed=tags)

    def _parse_photos(self, soup: BeautifulSoup) -> Photos:
        urls: list[str] = []
        og_image = soup.select_one('meta[property="og:image"]')
        if og_image and og_image.get("content"):
            urls.append(og_image["content"])
        return Photos(urls=urls, vision_analysis=None)

    def _build_verification_links(self, soup: BeautifulSoup, detail_url: str) -> list[VerificationLink]:
        links = [
            VerificationLink(
                label="매물 상세페이지(KB차차차)",
                url=detail_url,
                note="사진·판매자 정보 등 원본 그대로 확인",
            )
        ]

        checkup_link = soup.select_one("a[data-link-url]")
        if checkup_link and checkup_link.get("data-link-url"):
            links.append(VerificationLink(
                label="성능점검기록부 원본 보기(카모두)",
                url=checkup_link["data-link-url"],
                note="외판교환/프레임손상 등 부위별 상세는 이 제3자 성능점검기관 리포트에서 직접 확인",
            ))

        plate_text = self._spec_table(soup).get("차량정보", "")
        plate_match = re.search(r"[0-9]{2,3}[가-힣][0-9]{4}", plate_text)
        links.append(VerificationLink(
            label="카히스토리 공식 조회(보험개발원)",
            url=CARHISTORY_URL,
            note=(
                f"차량번호 '{plate_match.group()}'를 직접 입력해 조회(유료, 본인/차량 인증 필요)" if plate_match
                else "차량번호를 직접 입력해 조회(유료, 본인/차량 인증 필요)"
            ),
        ))

        return links
