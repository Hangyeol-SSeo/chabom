"""정규화된 매물 데이터 모델 (스펙 2장).

사이트별 어댑터는 각자의 원본 응답(HTML/JSON)을 이 스키마로 변환해야 한다.
스코어링 엔진(scoring/)은 이 모듈에만 의존하며, 사이트별 구조를 알 필요가 없다.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Optional


def parse_date(value: Optional[str]) -> Optional[date]:
    """'YYYY-MM-DD' 문자열을 date로 변환. None/빈값/파싱 실패 시 None."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


@dataclass
class Vehicle:
    make: str = ""
    model: str = ""
    trim: str = ""
    model_year: Optional[int] = None
    first_registration_date: Optional[str] = None
    mileage_km: Optional[int] = None
    transmission: str = ""  # manual | automatic
    fuel_type: str = ""  # gasoline | diesel | lpg | hybrid | bifuel | ev
    body_type: str = ""
    price_krw: Optional[int] = None
    new_car_price_krw: Optional[int] = None
    region: str = ""
    # 스펙 2장에 없는 이 프로젝트의 확장 필드(VerificationLink와 동일한 취지). 일부 "중고차 매물"은
    # 실제로는 장기렌트 승계(소유권 이전이 아니라 월 렌트료를 이어받는 계약) 매물이며, 일반 중고차와는
    # 판단 기준 자체가 다르다. bobaedream_adapter가 상세페이지의 '월렌트료'/'렌트기간'/'승계지원금' 같은
    # 필드 존재로 이를 감지해 채운다. 값: "sale" | "rental_transfer".
    listing_type: str = "sale"


@dataclass
class OwnerChangeLogEntry:
    date: Optional[str] = None
    note: str = ""


@dataclass
class HistoryEvent:
    category: str = ""
    date: str = ""  # 원문이 월까지만 제공되면 YYYY-MM, 일자까지 제공되면 YYYY-MM-DD
    summary: str = ""
    details: dict[str, str] = field(default_factory=dict)


@dataclass
class DamageClaim:
    date: Optional[str] = None
    amount_krw: int = 0
    parts_cost: Optional[int] = None
    labor_cost: Optional[int] = None
    paint_cost: Optional[int] = None


@dataclass
class OtherPartyDamageClaim:
    date: Optional[str] = None
    amount_krw: int = 0


@dataclass
class InfoUnavailablePeriod:
    start: Optional[str] = None
    end: Optional[str] = None


@dataclass
class UsageHistory:
    rental_used: bool = False
    taxi_used: bool = False
    business_used: bool = False

    def any_commercial_use(self) -> bool:
        return self.rental_used or self.taxi_used or self.business_used


@dataclass
class InsuranceHistory:
    usage_history: UsageHistory = field(default_factory=UsageHistory)
    owner_change_count: Optional[int] = 0
    owner_change_log: list[OwnerChangeLogEntry] = field(default_factory=list)
    number_change_count: Optional[int] = None
    usage_change_count: Optional[int] = None
    history_events: list[HistoryEvent] = field(default_factory=list)
    history_warnings: dict[str, str] = field(default_factory=dict)
    history_detail_status: str = "unavailable"  # available | login_required | unavailable
    coverage_verified: bool = False
    own_damage_count: Optional[int] = None
    own_damage_total_krw: Optional[int] = None
    other_party_damage_count: Optional[int] = None
    other_party_damage_total_krw: Optional[int] = None
    own_damage_claims: list[DamageClaim] = field(default_factory=list)
    other_party_damage_claims: list[OtherPartyDamageClaim] = field(default_factory=list)
    info_unavailable_periods: list[InfoUnavailablePeriod] = field(default_factory=list)
    # 딜러가 이력 조회 결과를 비공개 처리했는지 여부. None = 알 수 없음(크롤러가 미확인).
    history_disclosed: Optional[bool] = None
    # 2026-09-18 추가(체크리스트 검증 기능): 삼상태(모름/있음/없음)로 명시적으로 관리한다 — "빈 값"과
    # "확인해서 없다고 확인됨"을 혼동하면 1종 오류(부실차량을 정상으로 오판)로 이어질 수 있어서다.
    # None = 확인 안 됨(체크리스트에서 FAIL과 동일하게 취급), True = 있음, False = 확인 결과 없음.
    flood_damage: Optional[bool] = None
    total_loss: Optional[bool] = None
    theft: Optional[bool] = None


@dataclass
class ThirdPartyInspection:
    provider: str = "none"  # encar_diagnosis | none | other
    panel_ok: Optional[bool] = None
    frame_ok: Optional[bool] = None


@dataclass
class PerformanceRecord:
    panel_exchange: list[str] = field(default_factory=list)
    panel_repairs: list[str] = field(default_factory=list)
    frame_damage: list[str] = field(default_factory=list)
    leak_records: list[str] = field(default_factory=list)
    inspection_results: dict[str, str] = field(default_factory=dict)
    record_url: str = ""
    record_available: bool = False
    record_images: list[str] = field(default_factory=list)  # 기록부가 스캔 이미지로만 등록된 경우
    third_party_inspection: ThirdPartyInspection = field(default_factory=ThirdPartyInspection)


@dataclass
class Dealer:
    dealer_id: str = ""
    join_year: Optional[int] = None
    total_sales_count: Optional[int] = None
    active_listings_count: Optional[int] = None
    region: str = ""
    # 2026-09-18 추가(딜러 블랙리스트 기능): 이름은 동명이인이 있을 수 있어 교차 사이트 매칭 키로 쓸 수
    # 없다 — 전화번호를 보조 매칭 키로 쓴다(storage/dealers.py 참고). 숫자만 남긴 형태로 저장한다.
    phone: str = ""
    # 블랙리스트 등록 화면에 표시할 사람이 읽을 수 있는 이름. dealer_id(사이트별 불투명 키)와 달리
    # 식별용 키로는 쓰지 않는다 — 동명이인 문제 때문(위 phone 필드 설명 참고).
    display_name: str = ""


@dataclass
class ListingText:
    description_raw: str = ""
    claims_parsed: list[str] = field(default_factory=list)


@dataclass
class Photos:
    urls: list[str] = field(default_factory=list)
    vision_analysis: Optional[dict] = None


@dataclass
class VerificationLink:
    """크롤러가 직접 수집/판단하지 못하는 정보를 사용자가 스스로 확인할 수 있도록 안내하는 링크.

    스펙 2장의 원본 데이터 스키마에는 없는 이 프로젝트의 자체 확장 필드다. 특히
    insurance_history/performance_record처럼 크롤링이 막혀 있거나(엔카 등 robots.txt 차단)
    사이트가 요약치만 노출하는 경우(보배드림), 하드필터·스코어를 맹신하지 말고 사람이 직접
    원본을 확인하도록 유도하는 용도다.
    """

    label: str
    url: str
    note: str = ""


@dataclass
class Listing:
    listing_id: str
    source: str  # encar | kcar | kbchachacha | bobaedream | ...
    url: str = ""
    vehicle: Vehicle = field(default_factory=Vehicle)
    insurance_history: InsuranceHistory = field(default_factory=InsuranceHistory)
    performance_record: PerformanceRecord = field(default_factory=PerformanceRecord)
    dealer: Dealer = field(default_factory=Dealer)
    listing_text: ListingText = field(default_factory=ListingText)
    photos: Photos = field(default_factory=Photos)
    tire_brand: Optional[str] = None
    verification_links: list[VerificationLink] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "Listing":
        vehicle = Vehicle(**data.get("vehicle", {}))

        ih = data.get("insurance_history", {}) or {}
        usage = UsageHistory(**ih.get("usage_history", {}))
        owner_log = [OwnerChangeLogEntry(**e) for e in ih.get("owner_change_log", [])]
        events = [HistoryEvent(**e) for e in ih.get("history_events", [])]
        own_claims = [DamageClaim(**c) for c in ih.get("own_damage_claims", [])]
        other_claims = [OtherPartyDamageClaim(**c) for c in ih.get("other_party_damage_claims", [])]
        unavailable = [InfoUnavailablePeriod(**p) for p in ih.get("info_unavailable_periods", [])]
        insurance_history = InsuranceHistory(
            usage_history=usage,
            owner_change_count=ih.get("owner_change_count", 0),
            owner_change_log=owner_log,
            number_change_count=ih.get("number_change_count"),
            usage_change_count=ih.get("usage_change_count"),
            history_events=events,
            history_warnings=ih.get("history_warnings", {}),
            history_detail_status=ih.get("history_detail_status", "unavailable"),
            coverage_verified=ih.get("coverage_verified", False),
            own_damage_count=ih.get("own_damage_count"),
            own_damage_total_krw=ih.get("own_damage_total_krw"),
            other_party_damage_count=ih.get("other_party_damage_count"),
            other_party_damage_total_krw=ih.get("other_party_damage_total_krw"),
            own_damage_claims=own_claims,
            other_party_damage_claims=other_claims,
            info_unavailable_periods=unavailable,
            history_disclosed=ih.get("history_disclosed"),
            flood_damage=ih.get("flood_damage"),
            total_loss=ih.get("total_loss"),
            theft=ih.get("theft"),
        )

        pr = data.get("performance_record", {}) or {}
        tpi = ThirdPartyInspection(**pr.get("third_party_inspection", {}))
        performance_record = PerformanceRecord(
            panel_exchange=pr.get("panel_exchange", []),
            panel_repairs=pr.get("panel_repairs", []),
            frame_damage=pr.get("frame_damage", []),
            leak_records=pr.get("leak_records", []),
            inspection_results=pr.get("inspection_results", {}),
            record_url=pr.get("record_url", ""),
            record_available=pr.get("record_available", False),
            record_images=pr.get("record_images", []),
            third_party_inspection=tpi,
        )

        dealer = Dealer(**data.get("dealer", {}))
        listing_text = ListingText(**data.get("listing_text", {}))
        photos = Photos(**data.get("photos", {}))
        verification_links = [VerificationLink(**v) for v in data.get("verification_links", [])]

        return Listing(
            listing_id=data["listing_id"],
            source=data.get("source", ""),
            url=data.get("url", ""),
            vehicle=vehicle,
            insurance_history=insurance_history,
            performance_record=performance_record,
            dealer=dealer,
            listing_text=listing_text,
            verification_links=verification_links,
            photos=photos,
            tire_brand=data.get("tire_brand"),
        )
