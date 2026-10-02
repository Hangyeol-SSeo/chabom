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
from calendar import monthrange
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from crawler.browser_fetch import BrowserFetcher
from normalizer.schema import (
    Dealer,
    DamageClaim,
    HistoryEvent,
    InfoUnavailablePeriod,
    InsuranceHistory,
    Listing,
    ListingText,
    OtherPartyDamageClaim,
    OwnerChangeLogEntry,
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
AUTH_STATE_PATH = Path(__file__).resolve().parents[2] / "data" / "kcar_auth_state.json"

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
        self.fetcher = fetcher or BrowserFetcher(storage_state=AUTH_STATE_PATH)
        self.timeout_sec = timeout_sec

    def parse_detail(self, url: str) -> Listing:
        m = re.search(r"i_sCarCd=([\w]+)", url)
        if not m:
            raise RuntimeError(f"케이카 상세 URL에서 i_sCarCd를 찾을 수 없습니다(carCd= 파라미터는 다른 이름입니다): {url}")
        listing_id = m.group(1)

        page = self.fetcher.new_page()
        try:
            try:
                page.goto(SEARCH_WARMUP_URL, wait_until="domcontentloaded", timeout=self.timeout_sec * 1000)
                page.wait_for_timeout(1500)
            except Exception as exc:
                logger.info("K Car 검색 화면 준비 실패(상세 조회 계속): %s", type(exc).__name__)
            page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_sec * 1000)
            page.wait_for_selector(".carInfoKeyArea", timeout=self.timeout_sec * 1000)
            page.wait_for_timeout(2500)
            soup = BeautifulSoup(page.content(), "html.parser")
            vehicle = self._parse_vehicle(soup)
            if vehicle.price_krw is None and vehicle.model_year is None:
                raise RuntimeError(
                    f"매물 데이터를 확인할 수 없습니다(판매완료/삭제되었거나 페이지 구조가 바뀌었을 수 있음): {url}"
                )
            performance_record = self._parse_performance_record(soup)
            insurance_history = self._parse_insurance_history(soup)
            self._enrich_insurance_from_dialogs(page, insurance_history)
            self._enrich_performance_from_dialog(page, performance_record, url)
        finally:
            page.close()

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

        def counts(section_id: str) -> Optional[tuple[int, int]]:
            section = soup.select_one(f"#{section_id} ul.labels")
            if not section:
                return None
            texts = [li.get_text(strip=True) for li in section.select("li")]
            panel = next((_to_int(t) or 0 for t in texts if "판금" in t), 0)
            exchange = next((_to_int(t) or 0 for t in texts if "교환" in t), 0)
            return (panel, exchange)

        ext_counts, frame_counts = counts("ext"), counts("frame")
        ext_panel, ext_exchange = ext_counts or (0, 0)
        frame_panel, frame_exchange = frame_counts or (0, 0)

        panel_exchange = []
        panel_repairs = []
        if ext_exchange:
            panel_exchange.append(f"외판 교환 {ext_exchange}건(케이카 진단)")
        if ext_panel:
            panel_repairs.append(f"외판 판금 {ext_panel}건(케이카 진단)")

        frame_ok = frame_panel == 0 and frame_exchange == 0 if frame_counts else None
        frame_damage = []
        if frame_ok is False:
            frame_damage.append(f"프레임 판금 {frame_panel}건/교환 {frame_exchange}건(케이카 진단)")

        return PerformanceRecord(
            panel_exchange=panel_exchange,
            panel_exchange_count=ext_exchange if ext_counts else None,
            panel_repairs=panel_repairs,
            frame_damage=frame_damage,
            inspection_results={
                "케이카 진단 · 외판": f"판금 {ext_panel}건 · 교환 {ext_exchange}건",
                "케이카 진단 · 주요골격": f"판금 {frame_panel}건 · 교환 {frame_exchange}건",
            } if ext_counts and frame_counts else {},
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

        return InsuranceHistory(history_disclosed=history_disclosed, owner_change_count=None)

    @staticmethod
    def _normalize_date(raw: str, *, end_of_month: bool = False) -> str:
        match = re.search(r"(\d{4})[년.\-/]\s*(\d{1,2})(?:[월.\-/]\s*(\d{1,2}))?", raw)
        if not match:
            return raw.strip()
        year, month = int(match.group(1)), int(match.group(2))
        if not 1 <= month <= 12:
            return raw.strip()
        day = int(match.group(3)) if match.group(3) else monthrange(year, month)[1] if end_of_month else 1
        return f"{year:04d}-{month:02d}-{day:02d}"

    @staticmethod
    def _status_flag(raw: str) -> Optional[bool]:
        value = raw.strip()
        if value == "없음" or value == "0회":
            return False
        if value == "있음" or re.search(r"[1-9]\d*\s*(?:회|건)", value):
            return True
        return None

    def _parse_insurance_dialog(self, soup: BeautifulSoup, history: InsuranceHistory) -> None:
        summary = soup.select_one(".el-dialog__body .hisBox")
        if not summary or not soup.find(string=re.compile("보험사고이력 상세 정보")):
            return

        values = {}
        for item in summary.select("li"):
            label, value = item.select_one("p"), item.select_one("strong")
            if label and value:
                values[label.get_text(" ", strip=True)] = value.get_text(" ", strip=True)
        if not {"소유자 변경", "차량번호 변경"}.issubset(values):
            return

        history.history_disclosed = True
        history.history_detail_status = "available"
        history.coverage_verified = False
        for label, attribute in (("전손 보험사고", "total_loss"), ("도난 보험사고", "theft"), ("침수 보험사고", "flood_damage")):
            setattr(history, attribute, self._status_flag(values.get(label, "")))
        for label, attribute in (("소유자 변경", "owner_change_count"), ("차량번호 변경", "number_change_count")):
            raw = values[label]
            setattr(history, attribute, 0 if raw == "없음" else _to_int(raw))
        for label, count_attr, amount_attr in (
            ("내차 피해", "own_damage_count", "own_damage_total_krw"),
            ("상대차 피해", "other_party_damage_count", "other_party_damage_total_krw"),
        ):
            raw = values.get(label, "")
            setattr(history, count_attr, 0 if raw == "없음" else _to_int(raw.split("회", 1)[0]))
            amount = re.search(r"\(([\d,]+)원\)", raw)
            setattr(history, amount_attr, _to_int(amount.group(1)) if amount else 0 if raw == "없음" else None)

        for heading in soup.select("h3"):
            if "자동차 특수 용도 이력 정보" not in heading.get_text(" ", strip=True):
                continue
            section = heading.parent.find_next_sibling(class_="hisBox")
            if not section:
                break
            for item in section.select("li"):
                label, value = item.select_one("p"), item.select_one("strong")
                if not label or not value:
                    continue
                flag = self._status_flag(value.get_text(" ", strip=True))
                label_text = label.get_text(" ", strip=True)
                if flag is True:
                    history.history_warnings[label_text] = value.get_text(" ", strip=True)
                if "대여" in label_text and flag is True:
                    history.usage_history.rental_used = True
                if "영업" in label_text and flag is True:
                    history.usage_history.taxi_used = True
                if "관용" in label_text and flag is True:
                    history.usage_history.business_used = True

        gap_box = soup.select_one(".boxDesc.insuBox")
        gap = gap_box.select_one(".insuTxt strong") if gap_box else None
        if gap:
            history.coverage_verified = True
            raw = gap.get_text(" ", strip=True)
            periods = raw.split("~", 1)
            if len(periods) == 2:
                history.info_unavailable_periods.append(InfoUnavailablePeriod(
                    start=self._normalize_date(periods[0]),
                    end=self._normalize_date(periods[1], end_of_month=True),
                ))
            else:
                history.info_unavailable_periods.append(InfoUnavailablePeriod())
            history.history_warnings["자차 보험 미가입 기간"] = raw
        elif gap_box and re.search(r"없음|없습니다|해당 없음", gap_box.get_text(" ", strip=True)):
            history.coverage_verified = True
            history.history_warnings["자차 보험 미가입 기간"] = "없음"
        else:
            history.history_warnings["자차 보험 미가입 기간"] = "미확인"

        usages = []
        for row in soup.select("table.hisTb tbody tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
            if len(cells) < 4:
                continue
            date = self._normalize_date(cells[0])
            if cells[3] and cells[3] != "-":
                usages.append(cells[3])
            if "변경" in cells[1]:
                history.history_events.append(HistoryEvent(category="소유자 변경", date=date, summary=cells[3]))
                history.owner_change_log.append(OwnerChangeLogEntry(date=date, note=cells[3]))
            if cells[2] not in {"", "-", "없음"}:
                history.history_events.append(HistoryEvent(category="차량번호 변경", date=date, summary=cells[2]))
        if usages:
            history.history_warnings["변경이력 표 차량용도"] = ", ".join(dict.fromkeys(usages))
            history.usage_change_count = sum(a != b for a, b in zip(usages, usages[1:])) if len(set(usages)) > 1 else None

        for accident in soup.select(".accList .accWrap"):
            date_tag = accident.select_one(".accTit span")
            date = self._normalize_date(date_tag.get_text(" ", strip=True)) if date_tag else None
            for cell in accident.select("table.cont td"):
                rows = cell.select("li")
                if not rows:
                    continue
                kind = rows[0].select_one(".dataList span")
                kind_text = kind.get_text(" ", strip=True) if kind else ""
                amount = 0
                costs = {}
                for row in rows:
                    label = row.select_one(".dataList span")
                    value = row.select_one(".dataList strong")
                    if label and value and "수리(견적)비용" in label.get_text(" ", strip=True):
                        amount = _to_int(value.get_text(" ", strip=True)) or 0
                    for pair in row.select(".price dl"):
                        key, number = pair.select_one("dt"), pair.select_one("dd")
                        if key and number:
                            costs[key.get_text(" ", strip=True)] = _to_int(number.get_text(" ", strip=True))
                if amount and kind_text == "내차 피해":
                    history.own_damage_claims.append(DamageClaim(
                        date=date, amount_krw=amount, parts_cost=costs.get("부품"),
                        labor_cost=costs.get("공임"), paint_cost=costs.get("도장"),
                    ))
                elif amount and kind_text == "상대차 피해":
                    history.other_party_damage_claims.append(OtherPartyDamageClaim(date=date, amount_krw=amount))

    def _parse_history_dialog(self, soup: BeautifulSoup, history: InsuranceHistory) -> None:
        for cell in soup.select("li.cell.toggle"):
            heading = cell.select_one(".cell-top .label")
            if not heading:
                continue
            category = heading.get_text(" ", strip=True)
            value = cell.select_one(".cell-top .value")
            raw_date = value.get_text(" ", strip=True) if value else ""
            date = self._normalize_date(raw_date) if raw_date else ""
            details = [p.get_text(" ", strip=True) for p in cell.select(".cell-content .dot-list > li > p")]
            details = [item for item in details if item and not item.startswith(("정보조회일", "위 정보는"))]
            summary = " · ".join(details[:4])
            existing = next((event for event in history.history_events if event.category == category and event.date == date), None)
            if existing:
                existing.summary = summary or existing.summary
            else:
                history.history_events.append(HistoryEvent(category=category, date=date, summary=summary))
                if category == "소유자 변경":
                    history.owner_change_log.append(OwnerChangeLogEntry(date=date, note=summary))
        if history.usage_change_count is None:
            usage_events = [event for event in history.history_events if "용도" in event.category and "변경" in event.category]
            if usage_events:
                history.usage_change_count = len(usage_events)
        history.history_events.sort(key=lambda item: item.date or "")

    def _enrich_insurance_from_dialogs(self, page, history: InsuranceHistory) -> None:
        dialogs = (
            ("#mkt_carInsuDtlPop", "보험이력 상세", self._parse_insurance_dialog),
            ("#mkt_insuHistTimeLine", "과거이력 상세", self._parse_history_dialog),
        )
        for selector, title, parse in dialogs:
            dialog = None
            try:
                page.locator(selector).click(timeout=5000)
                dialog = page.locator(".el-dialog__wrapper:visible").filter(has_text=title)
                dialog.wait_for(state="visible", timeout=10000)
                if title == "보험이력 상세":
                    dialog.locator(".hisBox li").first.wait_for(timeout=10000)
                else:
                    dialog.locator("li.cell.toggle").first.wait_for(timeout=10000)
                parse(BeautifulSoup(dialog.inner_html(), "html.parser"), history)
            except Exception as exc:
                logger.info("K Car %s 화면 미확인: %s", title, type(exc).__name__)
                if "/login" in page.url:
                    history.history_detail_status = "login_required"
            finally:
                if dialog and dialog.is_visible():
                    dialog.locator(".el-dialog__headerbtn").click(timeout=2000)

    def _enrich_performance_from_dialog(self, page, record: PerformanceRecord, detail_url: str) -> None:
        dialog = None
        try:
            page.locator("#mkt_carInspId").click(timeout=5000)
            dialog = page.locator(".el-dialog__wrapper:visible").filter(has_text="성능·상태 점검기록부")
            dialog.wait_for(state="visible", timeout=10000)
            images = dialog.locator(".carResultImg img")
            images.first.wait_for(timeout=10000)
            urls = images.evaluate_all("nodes => nodes.map(node => node.currentSrc || node.src)")
            record.record_images = [url for url in dict.fromkeys(urls)
                                    if urlparse(url).scheme == "https" and urlparse(url).hostname == "img.kcar.com"]
            record.record_available = bool(record.record_images)
            if record.record_available:
                record.record_url = detail_url
            for item in dialog.locator(".car_result_list li").all_text_contents():
                parts = item.strip().splitlines()
                if len(parts) > 1:
                    record.inspection_results[parts[0].strip()] = parts[-1].strip()
        except Exception as exc:
            logger.info("K Car 성능기록부 화면 미확인: %s", type(exc).__name__)
        finally:
            if dialog and dialog.is_visible():
                dialog.locator(".el-dialog__headerbtn").click(timeout=2000)

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
