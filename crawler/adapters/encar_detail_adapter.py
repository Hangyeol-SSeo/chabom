"""엔카 상세페이지 단건 조회 (2026-09-20 신설, `server.py`의 `/api/lookup` 전용).

**배경**: 사용자가 명시적으로 "엔카가 제일 중요하다"며 실제 매물 링크를 주고 조사를 요청했다.
`crawler/adapters/blocked_adapter.py::EncarAdapter`는 여전히 대량 크롤링을 전면 차단하는
스텁으로 남겨둔다 — 이 모듈은 그것과 별개로, **사용자가 직접 준 링크 1건만 여는** 단발성
조회 전용이다(케이카 단건 조회와 동일한 정책적 판단, `kcar_detail_adapter.py` 모듈 docstring
참고: "링크 1건을 여는 것은 자동화된 반복 수집이 아니라 사람이 그 링크를 클릭하는 것과
같다").

**실측 결과**:
- 실제 매물 상세 URL은 `https://fem.encar.com/cars/detail/{id}`다. 이 경로는 `fem.encar.com/
  robots.txt`에 `Disallow: /cars/detail/`로 명시되어 있다 — 케이카 때와 달리 "알고 보니 안
  막혀 있었다"가 아니라, 위 정책적 판단에 따라 의식적으로 예외 처리한 것이다.
- 페이지가 렌더링될 때 브라우저가 자동으로 `api.encar.com/v1/readside/...`를 호출해 보험이력/
  성능점검 데이터를 채운다. `api.encar.com/robots.txt`는 `Disallow: /`(전체 차단)이므로 **이
  모듈은 그 API를 절대 직접 호출하지 않는다** — Playwright가 페이지를 렌더링하며 자연스럽게
  발생시키는 부수 요청만 허용하고(사용자가 이 URL을 직접 브라우저로 열어도 똑같이 발생하는
  요청이다), 최종 렌더링된 페이지의 DOM/상태만 읽는다(케이카 어댑터와 동일한 원칙).
- 케이카와 달리 워밍업(검색 페이지 선방문) 없이 콜드 스타트로도 실데이터가 바로 렌더링됐다.
- 페이지에 `window.__PRELOADED_STATE__`라는 구조화된 JSON 상태 객체가 있어(Redux 스타일),
  제조사/모델/가격/주행거리/연료/변속기/딜러 연락처 등 대부분의 핵심 필드를 이 객체에서 바로
  읽을 수 있다 — DOM 텍스트를 긁는 것보다 훨씬 안정적이다. 다만 사고/보험이력(`cars.accident`)은
  이 객체에 채워지지 않는다(비동기로 나중에 불러와 DOM에만 반영됨) — 그 부분만 렌더링된 DOM의
  "차량이력" 섹션(`data-impression="차량이력"`, 해시되지 않은 안정적 속성)에서 읽는다.

**중요한 한계**: "차량이력" 섹션은 내차피해/타차가해 금액·건수는 명확히 구조화되어 있지만,
"특이 사항"이 정확히 무엇을 포괄하는지(전손·침수·도난·용도이력 등) 실측만으로는 확정할 수
없었다. 그래서 이 필드의 "없음"은 flood_damage/total_loss/theft를 자동으로 False로 채우는
근거로 쓰지 않는다(과대 확신 방지) — 반대로 "전손"/"침수"/"도난" 키워드가 명시적으로 나오면
True로는 반영한다(양성 신호는 신뢰하되 음성 신호는 신뢰하지 않는 비대칭적 처리, "모르겠으면
안 사면 된다" 원칙과 일치). 그래서 이 세 항목은 대부분 계속 사용자가 직접 확인해야 한다.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

from crawler.browser_fetch import BrowserFetcher
from normalizer.schema import (
    Dealer,
    DamageClaim,
    InsuranceHistory,
    Listing,
    ListingText,
    OtherPartyDamageClaim,
    PerformanceRecord,
    Photos,
    Vehicle,
    VerificationLink,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://fem.encar.com"
PHOTO_CDN = "https://ci.encar.com/carpicture"
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
_TRANSMISSION_MAP = {"오토": "automatic", "자동": "automatic", "수동": "manual"}
_SPECIAL_NOTE_KEYWORDS = ("전손", "침수", "도난")


def _to_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


class EncarDetailAdapter:
    source = "encar"

    def __init__(self, fetcher: Optional[BrowserFetcher] = None, timeout_sec: float = 25.0):
        self.fetcher = fetcher or BrowserFetcher()
        self.timeout_sec = timeout_sec

    def parse_detail(self, url: str) -> Listing:
        m = re.search(r"/cars/detail/(\d+)", url)
        if not m:
            raise RuntimeError(f"엔카 상세 URL 형식이 아닙니다(fem.encar.com/cars/detail/{{id}} 형태 필요): {url}")
        listing_id = m.group(1)

        page = self.fetcher.new_page()
        try:
            page.goto(url, timeout=self.timeout_sec * 1000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            state = page.evaluate("() => window.__PRELOADED_STATE__ ? window.__PRELOADED_STATE__.cars.base : null")
            html = page.content()
        except Exception as exc:
            raise RuntimeError(f"상세페이지를 가져오지 못했습니다: {url} ({exc})") from None
        finally:
            page.close()

        if not state or not state.get("category"):
            raise RuntimeError(
                f"매물 데이터를 확인할 수 없습니다(판매완료/삭제되었거나 페이지 구조가 바뀌었을 수 있음): {url}"
            )

        soup = BeautifulSoup(html, "html.parser")

        vehicle = self._build_vehicle(state)
        dealer = self._build_dealer(state, soup)
        insurance_history = self._parse_insurance_history(soup)
        listing_text = self._build_listing_text(state, soup)
        photos = self._build_photos(state)
        verification_links = self._build_verification_links(state, url)

        return Listing(
            listing_id=listing_id,
            source=self.source,
            url=url,
            vehicle=vehicle,
            insurance_history=insurance_history,
            performance_record=PerformanceRecord(),  # 부위별 프레임 데이터는 이 소스에서 확보 불가
            dealer=dealer,
            listing_text=listing_text,
            photos=photos,
            verification_links=verification_links,
        )

    # -- 개별 섹션 파서 -----------------------------------------------------

    def _build_vehicle(self, state: dict) -> Vehicle:
        category = state.get("category", {})
        advertisement = state.get("advertisement", {})
        spec = state.get("spec", {})

        model_year = _to_int(category.get("formYear"))
        year_month = category.get("yearMonth") or ""
        first_registration_date = None
        if len(year_month) == 6:
            first_registration_date = f"{year_month[:4]}-{year_month[4:]}-01"

        price = advertisement.get("price")
        origin_price = category.get("originPrice")

        transmission_raw = spec.get("transmissionName", "") or ""
        fuel_raw = spec.get("fuelName", "") or ""

        return Vehicle(
            make=category.get("manufacturerName", "") or "",
            model=category.get("modelName", "") or "",
            trim=" ".join(filter(None, [category.get("gradeName"), category.get("gradeDetailName")])),
            model_year=model_year,
            first_registration_date=first_registration_date,
            mileage_km=spec.get("mileage"),
            transmission=_TRANSMISSION_MAP.get(transmission_raw, ""),
            fuel_type=_FUEL_MAP.get(fuel_raw, fuel_raw),
            body_type=spec.get("bodyName", "") or "",
            price_krw=price * 10_000 if isinstance(price, (int, float)) else None,
            new_car_price_krw=origin_price * 10_000 if isinstance(origin_price, (int, float)) else None,
            region=state.get("contact", {}).get("address", "") or "",
        )

    def _build_dealer(self, state: dict, soup: BeautifulSoup) -> Dealer:
        contact = state.get("contact", {})
        display_name = ""
        name_btn = soup.select_one('button[data-enlog-dt-eventname="판매자정보"]')
        if name_btn:
            display_name = name_btn.get_text(" ", strip=True)
        return Dealer(
            dealer_id=contact.get("userId", "") or "",
            phone=re.sub(r"[^\d]", "", contact.get("no", "") or ""),
            region=contact.get("address", "") or "",
            display_name=display_name,
        )

    def _parse_insurance_history(self, soup: BeautifulSoup) -> InsuranceHistory:
        section = soup.select_one('[data-impression="차량이력"]')
        own_claims: list[DamageClaim] = []
        other_claims: list[OtherPartyDamageClaim] = []
        special_note = ""
        if section:
            for li in section.select("li"):
                label_tag = li.select_one("p")
                label = label_tag.get_text(strip=True) if label_tag else ""
                value_tag = li.select_one("em")
                value = value_tag.get_text(strip=True) if value_tag else li.get_text(strip=True).replace(label, "", 1)
                if label == "내차 피해":
                    amount, count = self._parse_claim_summary(value)
                    if count:
                        own_claims = self._split_claim(amount, count, own=True)
                elif label == "타차 가해":
                    amount, count = self._parse_claim_summary(value)
                    if count:
                        other_claims = self._split_claim(amount, count, own=False)
                elif label == "특이 사항":
                    special_note = value

        flood_damage = True if "침수" in special_note else None
        total_loss = True if "전손" in special_note else None
        theft = True if "도난" in special_note else None

        return InsuranceHistory(
            own_damage_claims=own_claims,
            other_party_damage_claims=other_claims,
            history_disclosed=True if section else None,
            flood_damage=flood_damage,
            total_loss=total_loss,
            theft=theft,
        )

    def _parse_claim_summary(self, text: str) -> tuple[int, int]:
        m = re.search(r"([\d,]+)\s*원\s*\(\s*(\d+)\s*회\s*\)", text)
        if not m:
            return (0, 0)
        return (int(m.group(1).replace(",", "")), int(m.group(2)))

    def _split_claim(self, total_amount: int, count: int, own: bool):
        if count <= 0:
            return []
        per_claim = round(total_amount / count)
        amounts = [per_claim] * (count - 1) + [total_amount - per_claim * (count - 1)]
        if own:
            return [DamageClaim(date=None, amount_krw=a) for a in amounts]
        return [OtherPartyDamageClaim(date=None, amount_krw=a) for a in amounts]

    def _build_listing_text(self, state: dict, soup: BeautifulSoup) -> ListingText:
        one_line = state.get("advertisement", {}).get("oneLineText", "") or ""
        section = soup.select_one('[data-impression="차량이력"]')
        special_note = ""
        if section:
            for li in section.select("li"):
                label_tag = li.select_one("p")
                if label_tag and label_tag.get_text(strip=True) == "특이 사항":
                    special_note = li.get_text(" ", strip=True).replace("특이 사항", "", 1).strip()
        claims = [kw for kw in _SPECIAL_NOTE_KEYWORDS if kw in special_note]
        description = one_line
        if special_note:
            description = f"{one_line} / 특이사항: {special_note}".strip(" /")
        return ListingText(description_raw=description, claims_parsed=claims)

    def _build_photos(self, state: dict) -> Photos:
        urls = []
        for p in state.get("photos", []) or []:
            path = p.get("path")
            if path:
                urls.append(f"{PHOTO_CDN}{path}")
        return Photos(urls=urls[:5])

    def _build_verification_links(self, state: dict, detail_url: str) -> list[VerificationLink]:
        links = [
            VerificationLink(label="매물 상세페이지(엔카)", url=detail_url, note="사진·판매자 설명 등 원본 그대로 확인"),
            VerificationLink(
                label="성능·상태 점검기록부 / 차량이력 자세히 보기",
                url=detail_url,
                note='상세페이지의 "성능기록부 자세히보기"/"차량이력 자세히 보기" 버튼을 직접 눌러 확인(팝업이라 직링크 불가)',
            ),
        ]
        plate = state.get("vehicleNo")
        links.append(VerificationLink(
            label="카히스토리 공식 조회(보험개발원)",
            url=CARHISTORY_URL,
            note=(f"차량번호 '{plate}'를 직접 입력해 조회(유료, 본인/차량 인증 필요)" if plate else "차량번호를 직접 입력해 조회"),
        ))
        return links
