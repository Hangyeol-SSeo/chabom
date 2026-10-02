"""보배드림(bobaedream.co.kr) 어댑터.

robots.txt 확인 결과(crawler/compliance.py): www.bobaedream.co.kr는 'User-agent: *'에
'Allow: /'이며 일반 봇에 대한 Disallow가 없다(Amazonbot 등 특정 UA만 차단). 목록/상세
페이지 모두 정적 서버 렌더링 HTML로 확인되어 이 어댑터를 구현했다.

**알려진 데이터 한계 (중요, README 참고)**
- `performance_record`는 상세페이지 "성능점검" 섹션의 "상세보기" 팝업
  (`/mycar/popup/mycarChart_5.php`)에서 읽는다(2026-10-02 추가 — 로그인 없이 열리는 정적 HTML).
  외판 14부위·주요골격 24부위의 교환/판금·용접/부식과 주요장치 점검 결과를 확보한다. 단,
  이 기록부는 **판매자가 직접 입력한 내용**이고, 성능점검 섹션이 아예 없는 매물(개인 매물 등)과
  기록부를 스캔 이미지로만 올린 매물은 부위별 판독이 불가하다 — 이 경우 프레임 손상 여부는
  계속 "미확인"으로 남고(이미지는 `record_images`로 화면에 그대로 보여준다) 사용자가 직접 확인한다.
- `insurance_history.own_damage_claims`/`other_party_damage_claims`는 상세페이지에 건수+총액
  집계로만 노출된다(예: "보험사고(내차피해) 3회 (3,868,140원)"). 건별 날짜/금액이 없으므로
  총액을 건수로 균등 배분한 근사치를 사용한다 — 실제 개별 사고 금액과 다를 수 있다.
- `insurance_history.usage_history`(렌트/택시/영업용)와 `info_unavailable_periods`는 상세페이지에서
  확인되지 않았다. 보험이력 팝업(/mycar/popup/mycarChart_B.php)에 더 있을 수 있으나, 실측 시
  15초 내 응답이 없어 기본적으로 비활성화하고 best-effort 옵션으로만 제공한다.

**대응**: 위 한계 때문에 크롤러가 자동으로 채우지 못하는 정보는 `Listing.verification_links`에
사람이 직접 클릭해 확인할 수 있는 링크로 담아 함께 제공한다(`_build_verification_links` 참고) —
매물 상세페이지, 보험이력 팝업(보배드림), 카히스토리 공식 조회, 성능점검기록부 원본.

**2026-09-17 개편**: 목록 조회를 "전량 수집 후 로컬 필터링"에서 "검색 조건을 사이트 목록
URL(`/mycar/mycar_list.php`)에 실어 서버가 직접 필터링한 결과만 받아오기"로 바꿨다(실측으로
`price_txt_1/2`·`km_txt_1/2`·`buyYear_sel1/2`·`fuelChk`·`methodChk`·`gubun` 등 파라미터가 모두
서버단에서 동작함을 확인 — 28,918건 중 조건에 맞는 1,245건만 응답하는 것을 확인했다).
전송 계층도 `requests`에서 Playwright(실브라우저)로 바꿨다(사용자 요청 — 개인 용도 도구라
브라우저 기반 수집을 우선한다).
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
    OtherPartyDamageClaim,
    DamageClaim,
    PerformanceRecord,
    Photos,
    ThirdPartyInspection,
    UsageHistory,
    Vehicle,
    VerificationLink,
)

CARHISTORY_URL = "https://www.carhistory.or.kr/main.car"

logger = logging.getLogger(__name__)

BASE_URL = "https://www.bobaedream.co.kr"

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

_CLAIM_KEYWORDS = [
    "1인소유", "무사고", "완전무사고",
    "렌트", "리스", "영업용", "법인", "관용",
]
# 주의: "침수"/"전손"/"도난"은 예전에 이 목록에 있었으나 제거했다 — 실제 페이지에는
# "자동차보험 특수사고 전손: 0 / 침수전손 : 0 / 침수분손 : 0 / 도난: 0" 형태로 나오는데,
# 단순 부분 문자열 매칭은 숫자가 0(없음)이어도 "도난"이라는 글자만 보고 있음으로 오판했다
# (2026-09-18 체크리스트 기능 실측 중 발견 — 이 오판이 결격 판정으로 이어지는 심각한 버그였다).
# 지금은 아래 _SPECIAL_ACCIDENT_RE로 숫자까지 정확히 파싱해서 삼상태로 반영한다.
_SPECIAL_ACCIDENT_RE = re.compile(
    r"전손:\s*(\d+)\s*/\s*침수전손\s*:\s*(\d+)\s*/\s*침수분손\s*:\s*(\d+)\s*/\s*도난:\s*(\d+)"
)

_LEAK_RE = re.compile(r"누유|누수|누출")

# 정규화된 fuel_type/transmission 값 -> 보배드림 목록 검색 파라미터 코드(실측 확인, 모듈 docstring 참고).
_FUEL_TO_PARAM = {
    "gasoline": "10",
    "diesel": "20",
    "lpg": "30",
    "hybrid": "60",  # 가솔린 하이브리드(가장 흔한 유형). 다른 하이브리드 조합은 목록에서 놓칠 수 있음.
    "ev": "100",
}
_TRANSMISSION_TO_PARAM = {"automatic": "A", "manual": "M"}


def _to_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


class BobaedreamAdapter(BaseAdapter):
    source = "bobaedream"

    def __init__(
        self,
        fetcher: Optional[BrowserFetcher] = None,
        rate_limiter: Optional[RateLimiter] = None,
        fetch_insurance_popup: bool = False,
        popup_timeout_sec: float = 5.0,
        request_timeout_sec: float = 15.0,
    ):
        self.fetcher = fetcher or BrowserFetcher()
        self.rate_limiter = rate_limiter or RateLimiter(min_delay_sec=1.0, max_delay_sec=3.0)
        self.fetch_insurance_popup = fetch_insurance_popup
        self.popup_timeout_sec = popup_timeout_sec
        self.request_timeout_sec = request_timeout_sec

    def _get_html(self, url: str, timeout: Optional[float] = None) -> Optional[str]:
        self.rate_limiter.wait()
        html = self.fetcher.get_html(url, timeout_sec=timeout or self.request_timeout_sec)
        if html is None:
            logger.warning("요청 실패: %s", url)
        return html

    def _build_list_url(self, params: SearchParams, page: int) -> str:
        """검색 조건을 실제 목록 URL 파라미터로 실어 서버단 필터링을 받는다(모듈 docstring 참고)."""
        gubun = (params.extra or {}).get("gubun", "K")  # K=국산차, I=수입차
        query: dict[str, str | int] = {"gubun": gubun, "page": page, "view_size": 30, "order": "S11"}
        if params.min_price_krw:
            query["price_txt_1"] = params.min_price_krw // 10_000
        if params.max_price_krw:
            query["price_txt_2"] = params.max_price_krw // 10_000
        if params.mileage_min_km is not None:
            query["km_txt_1"] = params.mileage_min_km
        if params.mileage_max_km is not None:
            query["km_txt_2"] = params.mileage_max_km
        if params.year_min:
            query["buyYear_sel1"] = params.year_min
        if params.year_max:
            query["buyYear_sel2"] = params.year_max
        if params.fuel_type and params.fuel_type in _FUEL_TO_PARAM:
            query["fuelChk"] = _FUEL_TO_PARAM[params.fuel_type]
        if params.transmission and params.transmission in _TRANSMISSION_TO_PARAM:
            query["methodChk"] = _TRANSMISSION_TO_PARAM[params.transmission]
        return f"{BASE_URL}/mycar/mycar_list.php?{urlencode(query)}"

    def fetch_listing_urls(self, params: SearchParams) -> Iterator[str]:
        gubun = (params.extra or {}).get("gubun", "K")
        seen: set[str] = set()
        for page in range(1, params.max_pages + 1):
            list_url = self._build_list_url(params, page)
            html = self._get_html(list_url)
            if html is None:
                continue
            soup = BeautifulSoup(html, "html.parser")
            links = soup.select('a[href*="mycar_view.php"]')
            page_ids = set()
            for a in links:
                href = a.get("href", "")
                m = re.search(r"no=(\d+)", href)
                if not m:
                    continue
                listing_id = m.group(1)
                if listing_id in seen:
                    continue
                seen.add(listing_id)
                page_ids.add(listing_id)
                yield f"{BASE_URL}/mycar/mycar_view.php?no={listing_id}&gubun={gubun}"
            if not page_ids:
                break  # 더 이상 매물이 없으면 조기 종료

    def parse_detail(self, url: str) -> Listing:
        m = re.search(r"no=(\d+)", url)
        listing_id = m.group(1) if m else url
        html = self._get_html(url)
        if html is None:
            raise RuntimeError(f"상세페이지를 가져오지 못했습니다: {url}")
        soup = BeautifulSoup(html, "html.parser")

        vehicle = self._parse_vehicle(soup)
        dealer = self._parse_dealer(soup)
        insurance_history = self._parse_insurance_summary(soup)
        listing_text = self._parse_listing_text(soup)
        photos = self._parse_photos(soup)
        performance_record = self._read_performance_record(soup)
        verification_links = self._build_verification_links(soup, url)

        if self.fetch_insurance_popup:
            self._try_enrich_from_popup(soup, url, insurance_history)

        return Listing(
            listing_id=listing_id,
            source=self.source,
            url=url,
            vehicle=vehicle,
            insurance_history=insurance_history,
            performance_record=performance_record,
            dealer=dealer,
            listing_text=listing_text,
            photos=photos,
            tire_brand=None,  # 사이즈만 노출, 브랜드는 사진 분석(vision) 필요
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

    def _parse_vehicle(self, soup: BeautifulSoup) -> Vehicle:
        spec = self._spec_table(soup)

        title_tag = soup.select_one("title")
        name_part = ""
        if title_tag:
            name_part = title_tag.get_text(strip=True).split("중고차")[0].strip()
        tokens = name_part.split()
        if tokens and re.match(r"^\d{4}$", tokens[0]):
            tokens = tokens[1:]
        make = tokens[0] if tokens else ""
        model = " ".join(tokens[1:]) if len(tokens) > 1 else ""

        model_year = None
        first_registration_date = None
        year_text = spec.get("연식", "")
        ym = re.match(r"(\d{4})\.(\d{2})", year_text)
        if ym:
            model_year = int(ym.group(1))
            first_registration_date = f"{ym.group(1)}-{ym.group(2)}-01"  # 일자는 월 단위 근사치

        mileage_km = _to_int(spec.get("주행거리"))

        transmission_raw = spec.get("변속기", "")
        transmission = "automatic" if "자동" in transmission_raw else ("manual" if "수동" in transmission_raw else "")

        fuel_raw = spec.get("연료", "")
        fuel_type = _FUEL_MAP.get(fuel_raw, fuel_raw)

        price_tag = soup.select_one(".price")
        price_krw = None
        if price_tag:
            price_krw = _to_int(price_tag.get_text())
            if price_krw is not None:
                price_krw *= 10_000  # 사이트 표기 단위: 만원

        region = ""
        addr_label = soup.find(string=re.compile("^주소$"))
        if addr_label:
            dd = addr_label.find_parent("dt")
            addr_dd = dd.find_next_sibling("dd") if dd else None
            if addr_dd:
                addr_text = addr_dd.get_text(strip=True)
                region_match = re.match(r"(\S+)\s+(\S+)", addr_text)
                region = f"{region_match.group(1)} {region_match.group(2)}" if region_match else addr_text

        # 일부 "매물"은 실제로는 장기렌트 승계 계약이다. 이 필드들(월렌트료/렌트기간/승계지원금)은
        # 스펙 표의 두 번째 <table>에 있어 _spec_table()(첫 번째 table만 파싱)로는 못 잡으므로
        # 페이지 전체 텍스트에서 직접 찾는다 — 가격 통계 추정보다 훨씬 정확한 결정론적 신호다.
        page_text_for_rental_check = soup.get_text(" ", strip=True)
        listing_type = (
            "rental_transfer"
            if any(k in page_text_for_rental_check for k in ("월렌트료", "렌트기간", "승계지원금"))
            else "sale"
        )

        return Vehicle(
            make=make,
            model=model,
            trim="",  # 사이트가 트림을 별도 필드로 노출하지 않아 모델명에 통합됨(위 docstring 참고)
            model_year=model_year,
            first_registration_date=first_registration_date,
            mileage_km=mileage_km,
            transmission=transmission,
            fuel_type=fuel_type,
            body_type="",  # 표본 페이지에서 확인 안 됨 — 실제 운용 시 재확인 필요
            price_krw=price_krw,
            new_car_price_krw=None,
            region=region,
            listing_type=listing_type,
        )

    def _parse_dealer(self, soup: BeautifulSoup) -> Dealer:
        # "판매중 9" / "판매완료 0" 형태로 <dd>판매중 <b><a>9</a></b></dd> 안에 텍스트와 숫자가 함께 있다.
        active_listings = None
        total_sales = None
        selling_node = soup.find(string=re.compile("판매중"))
        if selling_node:
            container = selling_node.find_parent("dd") or selling_node.parent
            if container:
                active_listings = _to_int(container.get_text())
        sold_node = soup.find(string=re.compile("판매완료"))
        if sold_node:
            container = sold_node.find_parent("dd") or sold_node.parent
            if container:
                total_sales = _to_int(container.get_text())

        dealer_id = ""
        seller_link = soup.select_one('a[href*="sellerID="]')
        if seller_link:
            sid_match = re.search(r"sellerID=([^&]+)", seller_link.get("href", ""))
            if sid_match:
                dealer_id = sid_match.group(1)

        region = ""
        addr_label = soup.find(string=re.compile("^주소$"))
        if addr_label:
            dt = addr_label.find_parent("dt")
            dd = dt.find_next_sibling("dd") if dt else None
            if dd:
                addr_text = dd.get_text(strip=True)
                region_match = re.match(r"(\S+)\s+(\S+)", addr_text)
                region = f"{region_match.group(1)} {region_match.group(2)}" if region_match else addr_text

        # 딜러 블랙리스트(storage/dealers.py)의 교차 사이트 보조 매칭 키로 쓴다 — 숫자만 남겨 정규화.
        phone = ""
        phone_label = soup.find(string=re.compile("^휴대폰$"))
        if phone_label:
            dt = phone_label.find_parent("dt")
            dd = dt.find_next_sibling("dd") if dt else None
            if dd:
                phone = re.sub(r"[^\d]", "", dd.get_text(strip=True))

        return Dealer(
            dealer_id=dealer_id,
            join_year=None,  # 상세페이지에 미노출(판매자 프로필 페이지 별도 확인 필요)
            total_sales_count=total_sales,  # 주의: 보배드림 플랫폼 내 판매완료 건수일 뿐 실제 총 판매대수 아님
            active_listings_count=active_listings,
            region=region,
            phone=phone,
        )

    def _parse_insurance_summary(self, soup: BeautifulSoup) -> InsuranceHistory:
        full_text = soup.get_text(" ", strip=True)

        owner_change_count = 0
        oc_match = re.search(r"소유자변경\s*([\d]+)\s*회\s*/\s*([\d]+)\s*회", full_text)
        if oc_match:
            # 사이트 표기 순서: "차량번호/소유자변경  {번호변경}회 / {소유자변경}회"
            owner_change_count = int(oc_match.group(2))

        # 주의: 사이트 원문 표기가 "내차피헤"(오타)로 되어 있어 그대로 매칭한다. 향후 사이트가
        # 오타를 수정하면 이 패턴도 갱신해야 한다.
        own_claims = self._parse_claim_group(full_text, "보험사고\\(내차피[헤해]\\)")
        other_claims_raw = self._parse_claim_group(full_text, "보험사고\\(타차가해\\)")
        other_claims = [OtherPartyDamageClaim(date=None, amount_krw=c.amount_krw) for c in other_claims_raw]

        insurance_section_present = "보험처리" in full_text
        history_disclosed = True if insurance_section_present else None

        # "자동차보험 특수사고 전손: 0 / 침수전손 : 0 / 침수분손 : 0 / 도난: 0" 형태를 정확히 숫자까지
        # 파싱해 삼상태로 반영한다 — 이 줄이 있다는 것 자체가 "보험사에서 조회해 확인했다"는
        # 뜻이므로, 못 찾으면(페이지 구조가 다르거나 조회가 안 된 경우) None(확인 안 됨)으로 둔다.
        total_loss: Optional[bool] = None
        flood_damage: Optional[bool] = None
        theft: Optional[bool] = None
        m = _SPECIAL_ACCIDENT_RE.search(full_text)
        if m:
            total_loss_count, flood_total_count, flood_partial_count, theft_count = (int(g) for g in m.groups())
            total_loss = total_loss_count > 0
            flood_damage = (flood_total_count + flood_partial_count) > 0
            theft = theft_count > 0

        return InsuranceHistory(
            usage_history=UsageHistory(),  # 상세페이지에서 확인 불가 — 기본값(False) 유지, README 한계 참고
            owner_change_count=owner_change_count,
            owner_change_log=[],
            own_damage_claims=own_claims,
            other_party_damage_claims=other_claims,
            info_unavailable_periods=[],  # 상세페이지에서 확인 불가
            history_disclosed=history_disclosed,
            total_loss=total_loss,
            flood_damage=flood_damage,
            theft=theft,
        )

    def _parse_claim_group(self, full_text: str, label_pattern: str) -> list[DamageClaim]:
        m = re.search(label_pattern + r"\s*([\d]+)\s*회\s*\(([\d,]+)\s*원\)", full_text)
        if not m:
            return []
        count = int(m.group(1))
        total_amount = int(m.group(2).replace(",", ""))
        if count <= 0:
            return []
        # 사이트는 건수+총액만 노출하고 건별 금액은 노출하지 않는다.
        # 총액을 건수로 균등 배분한 근사치이며, 실제 개별 사고 금액과 다를 수 있다(어댑터 docstring 참고).
        per_claim = round(total_amount / count)
        claims = [DamageClaim(date=None, amount_krw=per_claim) for _ in range(count - 1)]
        claims.append(DamageClaim(date=None, amount_krw=total_amount - per_claim * (count - 1)))
        return claims

    def _parse_listing_text(self, soup: BeautifulSoup) -> ListingText:
        full_text = soup.get_text(" ", strip=True)
        claims_parsed = [kw for kw in _CLAIM_KEYWORDS if kw in full_text]
        return ListingText(description_raw="", claims_parsed=claims_parsed)

    def _parse_photos(self, soup: BeautifulSoup) -> Photos:
        # og:image는 항상 사이트 로고로 고정되어 있어(실측 확인) 쓸모가 없다 — 실제 매물 사진은
        # CyberCar 파일 서버 경로의 갤러리 <img> 태그에 있다. 프로토콜 상대 URL(//...)이라 https:를 붙인다.
        urls: list[str] = []
        for img in soup.select('img[src*="CyberCar"]'):
            src = img.get("src", "")
            if src.startswith("//"):
                src = "https:" + src
            if src and src not in urls:
                urls.append(src)
        return Photos(urls=urls[:10], vision_analysis=None)

    def _find_insurance_popup_url(self, soup: BeautifulSoup) -> Optional[str]:
        popup_link = soup.select_one('a[onclick*="mycarChart"]')
        if not popup_link:
            return None
        onclick_attr = popup_link.get("onclick", "")
        url_match = re.search(r"fNewWin\('([^']+)'", onclick_attr)
        if not url_match:
            return None
        return BASE_URL + url_match.group(1)

    def _find_performance_popup_url(self, soup: BeautifulSoup) -> Optional[str]:
        button = soup.select_one('.info-check button[onclick*="mycarChart_5"]')
        if not button:
            return None
        url_match = re.search(r"fNewWin\('([^']+)'", button.get("onclick", ""))
        return BASE_URL + url_match.group(1) if url_match else None

    def _read_performance_record(self, soup: BeautifulSoup) -> PerformanceRecord:
        """성능점검 "상세보기" 팝업을 열어 부위별 결과를 읽는다. 못 읽으면 미확인으로 둔다."""
        popup_url = self._find_performance_popup_url(soup)
        if not popup_url:
            return PerformanceRecord()
        html = self._get_html(popup_url)
        if html is None:
            return PerformanceRecord(record_url=popup_url)
        record = self._parse_performance_record(BeautifulSoup(html, "html.parser"))
        record.record_url = popup_url
        return record

    def _parse_performance_record(self, soup: BeautifulSoup) -> PerformanceRecord:
        record = PerformanceRecord()
        panel, frame = soup.select_one("#ex_history"), soup.select_one("#in_history")
        if not panel or not frame:
            # 기록부를 스캔 이미지로만 올린 매물 — 부위별 판독 불가, 이미지만 넘긴다.
            for img in soup.select(".scroll-area img"):
                src = img.get("src", "")
                record.record_images.append("https:" + src if src.startswith("//") else src)
            return record
        record.record_available = True

        for part, state in self._parse_state_table(panel):
            target = record.panel_exchange if "교환" in state else record.panel_repairs
            target.append(f"{part} · {state}")
        record.frame_damage = [f"{part} · {state}" for part, state in self._parse_state_table(frame)]
        record.third_party_inspection = ThirdPartyInspection(
            provider="other", frame_ok=not record.frame_damage,
        )

        # 주요장치 점검표. 병합된(rowspan) 제목 칸은 아래 행들에도 이어 붙여
        # "원동기 · 오일누유 · 실린더헤드"처럼 완전한 항목명을 만든다.
        for table in soup.select(".p-performance-check .check-list .tbl-01 table"):
            carried: list[list] = []  # [제목, 남은 행 수]
            for row in table.select("tbody tr"):
                labels = [label for label, _ in carried]
                carried = [[label, left - 1] for label, left in carried if left > 1]
                for th in row.select("th"):
                    label = th.get_text(" ", strip=True)
                    labels.append(label)
                    if int(th.get("rowspan") or 1) > 1:
                        carried.append([label, int(th["rowspan"]) - 1])
                cells = row.select("td")
                if len(cells) != 1 or not labels:
                    continue
                value = cells[0].get_text(" ", strip=True)
                if not value:
                    continue
                key = " · ".join(labels)
                record.inspection_results[key] = value
                # "냉각수 누수" 묶음 아래의 "냉각수량: 적정"처럼 누유 여부가 아닌 항목은 제외한다.
                if _LEAK_RE.search(value) or (_LEAK_RE.search(labels[-1]) and value not in ("없음", "양호")):
                    record.leak_records.append(f"{key}: {value}")
        for th in soup.select(".p-performance-check .popup-section:not(.check-list) th"):
            label = th.get_text(" ", strip=True)
            td = th.find_next_sibling("td")
            if td and any(k in label for k in ("사고유무", "침수유무", "불법구조변경", "동일성확인", "보증유형")):
                record.inspection_results[label] = td.get_text(" ", strip=True)
        return record

    def _parse_state_table(self, table) -> list[tuple[str, str]]:
        """외판/주요골격 표에서 표시가 있는 (부위, 상태)만 돌려준다. 빈 칸은 이상 없음이다."""
        # 열 제목: "교환(교체)" / "판금/용접" / "부식"
        columns = [re.sub(r"\(.*?\)", "", th.get_text(" ", strip=True)) for th in table.select("thead th")][1:]
        found = []
        for row in table.select("tbody tr"):
            if not row.th:
                continue
            part = re.sub(r"^\d+\.\s*", "", row.th.get_text(" ", strip=True))
            for column, cell in zip(columns, row.select("td")):
                if cell.select_one(".i-mark"):
                    found.append((part, column))
        return found

    def _find_plate_number(self, soup: BeautifulSoup) -> Optional[str]:
        # 실제 DOM에서는 "차량번호 000가0000"처럼 라벨과 번호가 하나의 텍스트 노드에 붙어있다.
        full_text = soup.get_text(" ", strip=True)
        m = re.search(r"차량번호\s*([0-9]{2,3}[가-힣][0-9]{4})", full_text)
        return m.group(1) if m else None

    def _build_verification_links(self, soup: BeautifulSoup, detail_url: str) -> list[VerificationLink]:
        """크롤러가 확인하지 못하는(또는 요약치만 확보한) 정보를 사용자가 직접 확인할 수 있도록
        안내하는 링크 목록을 만든다. 실제 조회/스크래핑은 하지 않고 URL만 구성한다.
        """
        links = [
            VerificationLink(
                label="매물 상세페이지(보배드림)",
                url=detail_url,
                note="사진·판매자 설명 등 원본 그대로 확인",
            )
        ]

        popup_url = self._find_insurance_popup_url(soup)
        if popup_url:
            links.append(VerificationLink(
                label="보험이력 상세 조회(보배드림)",
                url=popup_url,
                note=(
                    "렌트/택시/영업용 이력, 소유자변경 일자별 상세 등이 있을 수 있으나 "
                    "크롤러가 자동 조회에 실패하는 경우가 있어(README 참고) 직접 클릭해 확인 필요"
                ),
            ))

        plate = self._find_plate_number(soup)
        links.append(VerificationLink(
            label="카히스토리 공식 조회(보험개발원)",
            url=CARHISTORY_URL,
            note=(
                f"차량번호 '{plate}'를 직접 입력해 조회(유료, 본인/차량 인증 필요)" if plate
                else "차량번호를 직접 입력해 조회(유료, 본인/차량 인증 필요)"
            ),
        ))

        performance_url = self._find_performance_popup_url(soup)
        if performance_url:
            links.append(VerificationLink(
                label="성능점검기록부 원본(보배드림)",
                url=performance_url,
                note="판매자가 입력한 기록부 — 실차·기록부 원본과 대조해 프레임(뼈대) 손상 여부 확인",
            ))
        else:
            links.append(VerificationLink(
                label="성능점검기록부 확인 안내",
                url=detail_url,
                note=(
                    "이 매물에는 등록된 성능점검기록부가 없다. 판매자에게 성능점검기록부 원본을 "
                    "별도로 요청해 프레임(뼈대) 손상 여부를 직접 확인할 것"
                ),
            ))

        return links

    def _try_enrich_from_popup(self, soup: BeautifulSoup, detail_url: str, insurance_history: InsuranceHistory) -> None:
        """보험이력 팝업에서 usage_history 등을 보강 시도(best-effort, 실패해도 무시).

        실측 결과 이 엔드포인트는 15초 내 응답이 없는 경우가 있었다(README 참고).
        기본값은 비활성화(fetch_insurance_popup=False)이며, 활성화 시에도 짧은 타임아웃으로
        실패를 빠르게 흡수한다.
        """
        popup_url = self._find_insurance_popup_url(soup)
        if not popup_url:
            return
        self.rate_limiter.wait()
        html = self.fetcher.get_html(popup_url, timeout_sec=self.popup_timeout_sec)
        if html is None:
            logger.info("보험이력 팝업 조회 실패(무시하고 진행): %s", popup_url)
            return

        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        if "렌트" in text:
            insurance_history.usage_history.rental_used = True
        if "택시" in text:
            insurance_history.usage_history.taxi_used = True
        if "영업용" in text:
            insurance_history.usage_history.business_used = True
