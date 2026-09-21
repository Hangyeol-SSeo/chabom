"""케이카 상세페이지 단건 조회 (2026-09-20 신설, `server.py`의 `/api/lookup` 전용).

**이 모듈이 따로 있는 이유**: `crawler/adapters/kcar_adapter.py`는 CLI 배치 크롤링용으로,
robots.txt가 명시적으로 금지한 `www.kcar.com` 상세페이지를 절대 열지 않고 `api.kcar.com`의
목록 API만 쓴다. 이 모듈은 **사용자가 직접 준 링크 1건만 여는** 단발성 조회 전용이며, 그
경우 상세페이지를 여는 것은 "자동화된 크롤러의 반복 수집"이 아니라 "사람이 그 링크를
클릭하는 것"과 동일하다고 판단했다(사용자 피드백, README 상단 참고). 실제로 열어보니 이
상세페이지는 예상외로 정보가 훨씬 풍부하다 — 케이카 소속 차량평가사가 직접 진단한 **외판
판금/교환 건수와 프레임(뼈대) 판금/교환 건수를 따로** 보여준다. 이건 보배드림·KB차차차
어디에도 없는, 프레임 손상을 실제로 판정할 수 있는 유일한 자동 확보 가능 신호다.

**중요한 기술적 함정(실측으로 발견)**: 이 상세페이지 URL은 `?carCd=`가 아니라
**`?i_sCarCd=`**를 쓴다 — 예전에 다른 파라미터명으로 시도했다가 세션 문제로 오판한 적이
있다. 게다가 검색 페이지를 거치지 않고 상세 URL을 콜드 스타트로 바로 열면 가격·진단결과가
전부 "0"으로 나오는 빈 템플릿만 받는다(robots.txt 차단이 아니라 이 사이트 프론트엔드가
검색 흐름에서 얻는 쿠키/세션을 기대하는 구조라서다) — 그래서 `crawler/browser_fetch.py`의
`warmup_url`로 검색 페이지를 먼저 방문한 뒤 상세 URL로 들어간다.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

from crawler.browser_fetch import BrowserFetcher
from normalizer.schema import (
    Dealer,
    InsuranceHistory,
    Listing,
    ListingText,
    PerformanceRecord,
    Photos,
    ThirdPartyInspection,
    Vehicle,
    VerificationLink,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.kcar.com"
SEARCH_WARMUP_URL = f"{BASE_URL}/bc/search"
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
_TRANSMISSION_MAP = {"오토": "automatic", "수동": "manual"}


def _to_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


class KcarDetailAdapter:
    source = "kcar"

    def __init__(self, fetcher: Optional[BrowserFetcher] = None, timeout_sec: float = 25.0):
        self.fetcher = fetcher or BrowserFetcher()
        self.timeout_sec = timeout_sec

    def parse_detail(self, url: str) -> Listing:
        m = re.search(r"i_sCarCd=([\w]+)", url)
        if not m:
            raise RuntimeError(f"케이카 상세 URL에서 i_sCarCd를 찾을 수 없습니다(carCd= 파라미터는 다른 이름입니다): {url}")
        listing_id = m.group(1)

        html = self.fetcher.get_html(
            url,
            timeout_sec=self.timeout_sec,
            warmup_url=SEARCH_WARMUP_URL,
            wait_selector=".carInfoKeyArea",
            post_wait_ms=2500,
        )
        if html is None:
            raise RuntimeError(f"상세페이지를 가져오지 못했습니다: {url}")
        soup = BeautifulSoup(html, "html.parser")

        vehicle = self._parse_vehicle(soup)
        if vehicle.price_krw is None and vehicle.model_year is None:
            # 매물이 삭제/판매완료됐을 때도 페이지 자체는 200으로 응답하고 빈 템플릿만 보여준다
            # (실측 확인) — 이 경우를 조용히 "빈 매물"로 반환하지 않고 명시적으로 실패시킨다.
            raise RuntimeError(
                f"매물 데이터를 확인할 수 없습니다(판매완료/삭제되었거나 페이지 구조가 바뀌었을 수 있음): {url}"
            )

        performance_record = self._parse_performance_record(soup)
        insurance_history = self._parse_insurance_history(soup)
        dealer = self._parse_dealer(soup)
        listing_text = self._parse_listing_text(soup)
        photos = self._parse_photos(soup)
        verification_links = self._build_verification_links(soup, url)

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
            verification_links=verification_links,
        )

    # -- 개별 섹션 파서 -----------------------------------------------------

    def _parse_vehicle(self, soup: BeautifulSoup) -> Vehicle:
        name_tag = soup.select_one("h2.carName")
        make, model = "", ""
        if name_tag:
            tokens = name_tag.get_text(strip=True).split()
            make = tokens[0] if tokens else ""
            model = " ".join(tokens[1:]) if len(tokens) > 1 else ""

        dot_items = [li.get_text(strip=True) for li in soup.select("ul.dotLists li")]
        model_year = None
        first_registration_date = None
        mileage_km = None
        fuel_type = ""
        transmission = ""
        price_krw = None
        for item in dot_items:
            ym = re.match(r"(\d{2})년\s*(\d{1,2})월식", item)
            if ym:
                model_year = 2000 + int(ym.group(1))
                first_registration_date = f"{model_year}-{int(ym.group(2)):02d}-01"
                continue
            if item.endswith("km"):
                mileage_km = _to_int(item)
                continue
            if item in _FUEL_MAP:
                fuel_type = _FUEL_MAP[item]
                continue
            if item in _TRANSMISSION_MAP:
                transmission = _TRANSMISSION_MAP[item]
                continue
            if item.endswith("만원"):
                price_krw = _to_int(item) * 10_000
                continue

        return Vehicle(
            make=make,
            model=model,
            trim="",
            model_year=model_year,
            first_registration_date=first_registration_date,
            mileage_km=mileage_km,
            transmission=transmission,
            fuel_type=fuel_type,
            body_type="",
            price_krw=price_krw,
            new_car_price_krw=None,
            region="",  # 상세페이지에 지역이 별도로 노출되지 않음(딜러=케이카 직영이라 매장 단위)
        )

    def _parse_performance_record(self, soup: BeautifulSoup) -> PerformanceRecord:
        """외판(#ext)과 프레임(#frame) 진단을 따로 읽는다 — 이 프로젝트에서 프레임 손상을
        실제로 판정할 수 있는 유일한 소스다(모듈 docstring 참고)."""

        def counts(section_id: str) -> tuple[int, int]:
            section = soup.select_one(f"#{section_id} ul.labels")
            if not section:
                return (0, 0)
            texts = [li.get_text(strip=True) for li in section.select("li")]
            panel = next((_to_int(t) or 0 for t in texts if "판금" in t), 0)
            exchange = next((_to_int(t) or 0 for t in texts if "교환" in t), 0)
            return (panel, exchange)

        ext_panel, ext_exchange = counts("ext")
        frame_panel, frame_exchange = counts("frame")

        panel_exchange = []
        if ext_panel or ext_exchange:
            panel_exchange.append(f"외판 판금 {ext_panel}건/교환 {ext_exchange}건(케이카 진단)")

        frame_ok = frame_panel == 0 and frame_exchange == 0
        frame_damage = []
        if not frame_ok:
            frame_damage.append(f"프레임 판금 {frame_panel}건/교환 {frame_exchange}건(케이카 진단)")

        return PerformanceRecord(
            panel_exchange=panel_exchange,
            frame_damage=frame_damage,
            third_party_inspection=ThirdPartyInspection(provider="kcar_diagnosis", frame_ok=frame_ok),
        )

    def _find_message_content(self, soup: BeautifulSoup, heading_contains: str) -> Optional[str]:
        """페이지에 `div.messageContent`가 여러 개 있다(진단결과 총평/과거이력/보증안내/평가사
        추천 등, 실측으로 4개 확인) — 바로 앞 `.detail-tit` 제목으로 원하는 섹션을 정확히
        찾아야 한다. 첫 번째 것만 집으면(과거이력이 아니라 일반 총평을 가져오는 등) 엉뚱한
        문구를 사고/침수 관련 근거로 오인할 수 있다."""
        for heading in soup.select(".detail-tit"):
            if heading_contains in heading.get_text(strip=True):
                content = heading.find_next(class_="messageContent")
                if content:
                    return content.get_text(" ", strip=True)
        return None

    def _parse_insurance_history(self, soup: BeautifulSoup) -> InsuranceHistory:
        # "K Car가 찾은 차량 과거이력" 섹션 존재 = 케이카가 이력을 조회해 공개했다는 뜻.
        # 다만 이건 자유서술문이라 침수/전손/도난 여부를 안전하게 파싱할 수 없다 — 그 세
        # 항목은 여전히 사용자가 문구를 직접 읽고 답해야 한다(체크리스트에서 계속 미확인).
        history_note = self._find_message_content(soup, "과거이력")
        history_disclosed = True if history_note else None

        return InsuranceHistory(history_disclosed=history_disclosed)

    def _parse_accident_badge(self, soup: BeautifulSoup) -> str:
        """사고진단 배지(예: "단순수리", "무사고") — claims_parsed에 참고용으로만 담는다."""
        badge_tag = soup.select_one("i.icon-accident")
        if not badge_tag:
            return ""
        row = badge_tag.find_parent("li")
        value_tag = row.select_one("p.value b") if row else None
        return value_tag.get_text(strip=True) if value_tag else ""

    def _parse_dealer(self, soup: BeautifulSoup) -> Dealer:
        name_tag = soup.select_one("div.userName")
        phone_tag = soup.select_one("div.callGuide")
        display_name = name_tag.get_text(strip=True) if name_tag else "케이카"
        phone = re.sub(r"[^\d]", "", phone_tag.get_text(strip=True)) if phone_tag else ""
        return Dealer(dealer_id=phone, display_name=display_name, phone=phone)

    def _parse_listing_text(self, soup: BeautifulSoup) -> ListingText:
        note = self._find_message_content(soup, "과거이력")
        badge = self._parse_accident_badge(soup)
        return ListingText(
            description_raw=note or "",
            claims_parsed=[badge] if badge else [],
        )

    def _parse_photos(self, soup: BeautifulSoup) -> Photos:
        urls = []
        for img in soup.select('img[src*="3dcarpicture"][src*="/main/"]'):
            src = img.get("src", "")
            if src and src not in urls:
                urls.append(src)
        return Photos(urls=urls[:5])

    def _build_verification_links(self, soup: BeautifulSoup, detail_url: str) -> list[VerificationLink]:
        links = [
            VerificationLink(label="매물 상세페이지(케이카)", url=detail_url, note="사진·차량평가사 소견 등 원본 그대로 확인"),
            VerificationLink(
                label="성능·상태 점검기록부 원본 보기",
                url=detail_url,
                note='상세페이지의 "성능·상태 점검기록부 보기" 버튼을 직접 눌러 확인(팝업이라 직링크 불가)',
            ),
        ]
        plate_tag = soup.select_one("span.carNum")
        plate = plate_tag.get_text(strip=True) if plate_tag else None
        links.append(VerificationLink(
            label="카히스토리 공식 조회(보험개발원)",
            url=CARHISTORY_URL,
            note=(f"차량번호 '{plate}'를 직접 입력해 조회(유료, 본인/차량 인증 필요)" if plate else "차량번호를 직접 입력해 조회"),
        ))
        return links
