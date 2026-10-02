"""사용자가 제공한 Encar 매물 한 건의 렌더링된 상세·이력·성능 화면을 읽는다.

내부 데이터 API는 직접 호출하지 않는다. 보험 상세는 Encar 로그인 세션이 필요한
별도 화면이므로 새 브라우저 세션에서 로그인 화면으로 이동하면 그 사실을 명시한다.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

from crawler.browser_fetch import BrowserFetcher
from normalizer.schema import (
    Dealer,
    HistoryEvent,
    InfoUnavailablePeriod,
    InsuranceHistory,
    Listing,
    ListingText,
    OwnerChangeLogEntry,
    PerformanceRecord,
    Photos,
    ThirdPartyInspection,
    Vehicle,
    VerificationLink,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://fem.encar.com"
PHOTO_CDN = "https://ci.encar.com/carpicture"
CARHISTORY_URL = "https://www.carhistory.or.kr/main.car"
HISTORY_DETAIL_URL = "https://car.encar.com/history?carId={listing_id}"
PERFORMANCE_DETAIL_URL = "https://www.encar.com/md/sl/mdsl_regcar.do?method=inspectionViewNew&carid={listing_id}"
AUTH_STATE_PATH = Path(__file__).resolve().parents[2] / "data" / "encar_auth_state.json"

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
        self.fetcher = fetcher or BrowserFetcher(storage_state=AUTH_STATE_PATH)
        self.timeout_sec = timeout_sec

    def parse_detail(self, url: str) -> Listing:
        m = re.search(r"/cars/detail/(\d+)", url)
        if not m:
            raise RuntimeError(f"엔카 상세 URL 형식이 아닙니다(fem.encar.com/cars/detail/{{id}} 형태 필요): {url}")
        listing_id = m.group(1)

        page = self.fetcher.new_page()
        try:
            page.goto(url, timeout=self.timeout_sec * 1000, wait_until="domcontentloaded")
            try:
                page.wait_for_selector('[data-impression="차량이력"] li', timeout=8000)
            except Exception:
                pass
            state = page.evaluate("() => window.__PRELOADED_STATE__ ? window.__PRELOADED_STATE__.cars.base : null")
            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            insurance_history = self._parse_insurance_history(soup)
            self._enrich_insurance_from_detail(page, insurance_history)
            performance_record = self._read_performance_record(page, listing_id)
        except Exception as exc:
            raise RuntimeError(f"상세페이지를 가져오지 못했습니다: {url} ({exc})") from None
        finally:
            page.close()

        if not state or not state.get("category"):
            raise RuntimeError(
                f"매물 데이터를 확인할 수 없습니다(판매완료/삭제되었거나 페이지 구조가 바뀌었을 수 있음): {url}"
            )

        vehicle = self._build_vehicle(state)
        dealer = self._build_dealer(state, soup)
        listing_text = self._build_listing_text(state, soup)
        photos = self._build_photos(state)
        verification_links = self._build_verification_links(state, url)

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
        own_amount = own_count = other_amount = other_count = None
        special_note = ""
        if section:
            for li in section.select("li"):
                label_tag = li.select_one("p")
                label = label_tag.get_text(strip=True) if label_tag else ""
                value_tag = li.select_one("em")
                value = value_tag.get_text(strip=True) if value_tag else li.get_text(strip=True).replace(label, "", 1)
                if label == "내차 피해":
                    own_amount, own_count = self._parse_claim_summary(value)
                elif label == "타차 가해":
                    other_amount, other_count = self._parse_claim_summary(value)
                elif label == "특이 사항":
                    special_note = value

        flood_damage = True if "침수" in special_note else None
        total_loss = True if "전손" in special_note else None
        theft = True if "도난" in special_note else None

        return InsuranceHistory(
            owner_change_count=None,
            own_damage_count=own_count,
            own_damage_total_krw=own_amount,
            other_party_damage_count=other_count,
            other_party_damage_total_krw=other_amount,
            history_disclosed=None,  # 요약 노출만으로 상세 이력·공백까지 공개됐다고 볼 수 없음
            flood_damage=flood_damage,
            total_loss=total_loss,
            theft=theft,
        )

    def _parse_claim_summary(self, text: str) -> tuple[Optional[int], Optional[int]]:
        if text.strip() == "없음":
            return (0, 0)
        m = re.search(r"([\d,]+)\s*원\s*\(\s*(\d+)\s*회\s*\)", text)
        if not m:
            return (None, None)
        return (int(m.group(1).replace(",", "")), int(m.group(2)))

    @staticmethod
    def _normalize_history_date(raw: str) -> str:
        match = re.search(r"(\d{2,4})년\s*(\d{1,2})월(?:\s*(\d{1,2})일)?", raw)
        if not match:
            return raw.strip()
        year = int(match.group(1))
        if year < 100:
            year += 2000 if year <= date.today().year % 100 else 1900
        month = int(match.group(2))
        day = match.group(3)
        return f"{year:04d}-{month:02d}" + (f"-{int(day):02d}" if day else "")

    def _enrich_insurance_from_detail(self, page, history: InsuranceHistory) -> None:
        button = page.locator('button[data-enlog-dt-hit="detail_car_history"]')
        if not button.count():
            return
        popup = None
        try:
            with page.expect_popup(timeout=6000) as popup_info:
                button.click(timeout=6000)
            popup = popup_info.value
            popup.wait_for_load_state("domcontentloaded", timeout=10000)
            popup.wait_for_function(
                "() => location.pathname.includes('/login') || !!document.body?.innerText?.includes('항목순')",
                timeout=10000,
            )
            if "/login" in popup.url:
                history.history_detail_status = "login_required"
                return
            popup.get_by_role("button", name="항목순").wait_for(timeout=10000)
            popup.wait_for_function(
                r"""() => {
                    const match = (document.body?.innerText || '').match(/이력\s*총\s*(\d+)건/);
                    if (!match) return false;
                    return Number(match[1]) === document.querySelectorAll(
                        '[class*="OrderedByTimeHistory_timeline"] button[data-enlog-dt-eventname]'
                    ).length;
                }""",
                timeout=10000,
            )
            self._parse_history_timeline(popup, history)
            popup.get_by_role("button", name="항목순").click()
            popup.wait_for_selector('[class*="OrderedByItem_caution_list"] > li', timeout=10000)
            self._parse_history_warnings(BeautifulSoup(popup.content(), "html.parser"), history)
            history.history_disclosed = True
            history.history_detail_status = "available"
        except Exception as exc:
            logger.info("Encar 보험 상세 화면을 읽지 못함: %s", exc)
        finally:
            if popup:
                popup.close()

    def _parse_history_timeline(self, popup, history: InsuranceHistory) -> None:
        events = self._parse_history_events(BeautifulSoup(popup.content(), "html.parser"))
        cards = popup.locator('[class*="OrderedByTimeHistory_timeline"] button[data-enlog-dt-eventname]')
        for index, event in enumerate(events):
            card = cards.nth(index)
            drawer = popup.locator('[class*="Drawer-module_drawer"]')
            try:
                card.click(timeout=4000)
                drawer.wait_for(state="visible", timeout=3000)
                details = self._parse_history_drawer_details(
                    BeautifulSoup(drawer.inner_html(timeout=2000), "html.parser")
                )
                event.details = details
                exact_date = details.get("변경일자") or details.get("발생일자") or details.get("사고일자")
                if exact_date:
                    event.date = self._normalize_history_date(exact_date)
            except Exception as exc:
                logger.info("Encar 이력 사건 세부정보 미확인(%s): %s", event.category, exc)
            finally:
                if drawer.is_visible():
                    try:
                        drawer.locator('button[class*="DetailContentsLayer_close_btn"]').click(
                            force=True, timeout=1500,
                        )
                    except Exception as exc:
                        logger.info("Encar 이력 상세 창 닫기 재시도: %s", exc)
                        if drawer.is_visible():
                            popup.keyboard.press("Escape")
                    try:
                        drawer.wait_for(state="hidden", timeout=2000)
                    except Exception as exc:
                        logger.info("Encar 이력 상세 창이 남아 있어 후속 사건을 읽지 못함: %s", exc)
                        break

        self._apply_history_events(history, events)

    def _apply_history_events(self, history: InsuranceHistory, events: list[HistoryEvent]) -> None:
        history.history_events = events
        owners = [event for event in events if "소유자변경" in event.category or "소유주변경" in event.category]
        numbers = [event for event in events if re.search(
            r"번호\s*변경|변경\s*번호", " ".join((event.category, event.details.get("변경구분", "")))
        )]
        uses = [event for event in events if re.search(
            r"용도\s*변경|변경\s*용도", " ".join((event.category, event.details.get("변경구분", "")))
        )]
        history.owner_change_count = len(owners)
        history.owner_change_log = [OwnerChangeLogEntry(date=event.date, note=event.summary) for event in owners]
        history.number_change_count = len(numbers)
        history.usage_change_count = len(uses)

        for event in events:
            if "침수" in event.category:
                history.flood_damage = True
            if "전손" in event.category:
                history.total_loss = True
            if "도난" in event.category:
                history.theft = True
            if "택시" in event.category:
                history.usage_history.taxi_used = True
            if "렌트" in event.category or "대여" in event.category:
                history.usage_history.rental_used = True

    def _parse_history_events(self, soup: BeautifulSoup) -> list[HistoryEvent]:
        events = []
        for block in soup.select('[class*="OrderedByTimeHistory_timeline"]'):
            date_tag = block.select_one('[class*="OrderedByTimeHistory_date"]')
            date_value = self._normalize_history_date(date_tag.get_text(strip=True)) if date_tag else ""
            for button in block.select('button[data-enlog-dt-eventname]'):
                summary_tag = button.select_one('[class*="OrderedByTimeHistory_detail_txt"]')
                title_tag = button.select_one('[class*="OrderedByTimeHistory_tit"]')
                events.append(HistoryEvent(
                    category=title_tag.get_text(' ', strip=True) if title_tag else button.get('data-enlog-dt-eventname', ''),
                    date=date_value,
                    summary=summary_tag.get_text(" ", strip=True) if summary_tag else "",
                ))
        return events

    def _parse_history_drawer_details(self, soup: BeautifulSoup) -> dict[str, str]:
        details = {}
        for item in soup.select('ul[class*="DetailContentsLayer_info_list"] > li'):
            key_tag = item.select_one('span[class*="DetailContentsLayer_title"]')
            if not key_tag:
                continue
            key = key_tag.get_text(" ", strip=True)
            value_tag = item.select_one('em[class*="DetailContentsLayer_info_val"]')
            value = (value_tag.get_text(" ", strip=True) if value_tag
                     else item.get_text(" ", strip=True).replace(key, "", 1).strip())
            if key:
                details[key] = value
        return details

    def _parse_history_warnings(self, soup: BeautifulSoup, history: InsuranceHistory) -> None:
        warnings = {}
        for item in soup.select('[class*="OrderedByItem_caution_list"] > li'):
            label = item.select_one('[class*="OrderedByItem_txt"]')
            value = item.select_one('[class*="OrderedByItem_count"]')
            if label and value:
                warnings[label.get_text(" ", strip=True)] = value.get_text(" ", strip=True)
        history.history_warnings = warnings

        special = warnings.get("전손, 침수, 도난")
        if special == "없음":
            history.total_loss = history.flood_damage = history.theft = False
        if warnings.get("택시 등 영업용") not in (None, "없음"):
            history.usage_history.business_used = True
        if warnings.get("렌터카 등 대여용") not in (None, "없음"):
            history.usage_history.rental_used = True
        gap = warnings.get("자차 보험 미가입 기간")
        if gap is not None:
            history.coverage_verified = True
            if gap != "없음":
                history.info_unavailable_periods = [InfoUnavailablePeriod()]

    def _read_performance_record(self, page, listing_id: str) -> PerformanceRecord:
        record = PerformanceRecord()
        button = page.get_by_role("button", name="성능기록부 자세히보기")
        if not button.count():
            return record
        record.record_url = PERFORMANCE_DETAIL_URL.format(listing_id=listing_id)
        popup = None
        try:
            with page.expect_popup(timeout=6000) as popup_info:
                button.click(timeout=6000)
            popup = popup_info.value
            popup.wait_for_function(
                "() => /성능번호\\s*제\\s*\\d+/.test(document.body?.innerText || '')",
                timeout=10000,
            )
            # 점검번호가 보인 뒤에도 외판/골격 도면 항목이 약간 늦게 채워진다.
            popup.wait_for_timeout(700)
            record = self._parse_performance_record(BeautifulSoup(popup.content(), "html.parser"))
            record.record_url = popup.url
        except Exception as exc:
            logger.info("Encar 성능기록부 화면을 읽지 못함: %s", exc)
        finally:
            if popup:
                popup.close()
        return record

    def _parse_performance_record(self, soup: BeautifulSoup) -> PerformanceRecord:
        record = PerformanceRecord()
        sections = {tag.get_text(strip=True): tag.parent for tag in soup.select('strong.tit_canv')}
        frame = sections.get("주요골격")
        panel = sections.get("외판")
        if not frame or not panel:
            return record
        record.record_available = True

        for item in panel.select('ul.list_state > li'):
            if 'uiLankNone' in item.get('class', []):
                continue
            part = item.select_one('strong.tit_part')
            state = item.select_one('div.txt_state')
            description = f"{part.get_text(' ', strip=True) if part else item.get_text(' ', strip=True)} · {state.get_text(' ', strip=True) if state else '상태 미확인'}"
            if state and "교환" in state.get_text():
                record.panel_exchange.append(description)
            else:
                record.panel_repairs.append(description)

        frame_lists = [frame.select_one(f'ul.uiListLank{rank}') for rank in "ABC"]
        if all(frame_lists):
            for rank, items in zip("ABC", frame_lists):
                for item in items.select('li'):
                    if 'uiLankNone' in item.get('class', []):
                        continue
                    part = item.select_one('strong.tit_part')
                    state = item.select_one('div.txt_state')
                    part_name = part.get_text(' ', strip=True) if part else item.get_text(' ', strip=True)
                    state_name = state.get_text(' ', strip=True) if state else '상태 미확인'
                    record.frame_damage.append(f"{rank}랭크 {part_name} · {state_name}")
            record.third_party_inspection = ThirdPartyInspection(frame_ok=not record.frame_damage)

        for row in soup.select('tr'):
            selected = row.select('span.txt_state.on')
            labels = [th.get_text(' ', strip=True) for th in row.select('th')]
            if not selected or not labels:
                continue
            key = ' · '.join(labels)
            value = ', '.join(tag.get_text(' ', strip=True) for tag in selected)
            record.inspection_results[key] = value
            if ("누유" in value or "누수" in value) and "없음" not in value:
                record.leak_records.append(f"{key}: {value}")
        return record

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
        listing_id = re.search(r"/cars/detail/(\d+)", detail_url).group(1)
        links = [
            VerificationLink(label="매물 상세페이지(엔카)", url=detail_url, note="사진·판매자 설명 등 원본 그대로 확인"),
            VerificationLink(
                label="성능·상태 점검기록부 원본",
                url=PERFORMANCE_DETAIL_URL.format(listing_id=listing_id),
                note="Encar에 등록된 점검기록부 원본",
            ),
            VerificationLink(
                label="보험·소유자·번호·용도 변경 상세 이력",
                url=HISTORY_DETAIL_URL.format(listing_id=listing_id),
                note="Encar 로그인 후 이력 화면의 시간순·항목순을 확인",
            ),
        ]
        plate = state.get("vehicleNo")
        links.append(VerificationLink(
            label="카히스토리 공식 조회(보험개발원)",
            url=CARHISTORY_URL,
            note=(f"차량번호 '{plate}'를 직접 입력해 조회(유료, 본인/차량 인증 필요)" if plate else "차량번호를 직접 입력해 조회"),
        ))
        return links
