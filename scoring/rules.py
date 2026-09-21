"""스펙 5장의 개별 스코어링 규칙. 각 함수는 순수 함수이며 단위 테스트가 쉽도록 분리했다.

모든 규칙 함수는 `RuleResult | None`을 반환한다 (조건에 해당하지 않으면 None).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from normalizer.schema import Listing, parse_date


@dataclass
class RuleResult:
    rule_id: str
    delta: float
    reason: str
    confidence: str  # "높음" | "중간" | "낮음"


@dataclass
class HardFilterHit:
    rule_id: str
    reason: str


# ---------------------------------------------------------------------------
# 하드 필터 (5.1)
# ---------------------------------------------------------------------------

def check_hard_filters(listing: Listing, weights: dict) -> list[HardFilterHit]:
    hits: list[HardFilterHit] = []

    if listing.insurance_history.history_disclosed is False:
        hits.append(HardFilterHit(
            "history_not_disclosed",
            "딜러가 공시 가능한 이력 정보를 비공개 처리함 — 그 자체로 비정상 신호",
        ))

    if listing.performance_record.frame_damage:
        parts = ", ".join(listing.performance_record.frame_damage)
        hits.append(HardFilterHit(
            "frame_damage",
            f"뼈대(프레임) 손상 이력 확인됨({parts}) — 원칙적으로 배제 대상",
        ))

    threshold_years = weights["hard_filters"]["info_unavailable_years_threshold"]
    total_days = _total_unavailable_days(listing)
    if total_days >= threshold_years * 365:
        hits.append(HardFilterHit(
            "info_unavailable_too_long",
            f"이력 조회 불가 기간이 약 {total_days / 365:.1f}년으로 임계값({threshold_years}년) 이상 "
            "— 장기간 자차보험 미가입 추정, 사고 이력 은폐 가능성",
        ))

    return hits


def _total_unavailable_days(listing: Listing) -> int:
    total = 0
    for period in listing.insurance_history.info_unavailable_periods:
        start = parse_date(period.start)
        end = parse_date(period.end)
        if start and end and end > start:
            total += (end - start).days
    return total


# ---------------------------------------------------------------------------
# 리스크 스코어 규칙 (5.2)
# ---------------------------------------------------------------------------

def is_light_car(listing: Listing, weights: dict) -> bool:
    model = (listing.vehicle.model or "").strip()
    return any(keyword in model for keyword in weights.get("light_car_models", []))


def rule_single_owner(listing: Listing, weights: dict) -> Optional[RuleResult]:
    count = listing.insurance_history.owner_change_count
    w = weights["risk_rules"]
    if count <= 1:
        return RuleResult("single_owner", w["single_owner_bonus"], "1인 소유(명의변경 이력 없음)", "높음")
    return None


def rule_owner_change_penalty(listing: Listing, weights: dict) -> Optional[RuleResult]:
    count = listing.insurance_history.owner_change_count
    if count <= 1:
        return None
    w = weights["risk_rules"]
    per_change = w["owner_change_penalty_light_car"] if is_light_car(listing, weights) else w["owner_change_penalty"]
    delta = per_change * count
    return RuleResult(
        "owner_change_penalty",
        delta,
        f"명의변경 {count}회 확인(건당 {per_change}점{'· 경차 완화 적용' if is_light_car(listing, weights) else ''})",
        "중간",
    )


def _latest_claim_date(listing: Listing) -> Optional[date]:
    dates = []
    for c in listing.insurance_history.own_damage_claims:
        d = parse_date(c.date)
        if d:
            dates.append(d)
    for c in listing.insurance_history.other_party_damage_claims:
        d = parse_date(c.date)
        if d:
            dates.append(d)
    return max(dates) if dates else None


def rule_commercial_use(listing: Listing, weights: dict, today: Optional[date] = None) -> Optional[RuleResult]:
    usage = listing.insurance_history.usage_history
    if not usage.any_commercial_use():
        return None
    w = weights["risk_rules"]
    today = today or date.today()
    latest_claim = _latest_claim_date(listing)
    mitigated = False
    if latest_claim is not None:
        years_since = (today - latest_claim).days / 365.25
        mitigated = years_since >= w["commercial_use_mitigation_years"]

    kinds = []
    if usage.rental_used:
        kinds.append("렌트")
    if usage.taxi_used:
        kinds.append("택시")
    if usage.business_used:
        kinds.append("영업용")
    kind_text = "/".join(kinds)

    if mitigated:
        return RuleResult(
            "commercial_use_mitigated",
            w["commercial_use_penalty_mitigated"],
            f"{kind_text} 이력 있으나, 최근 사고처리 이후 {w['commercial_use_mitigation_years']}년 이상 "
            "지속 운행 기록으로 감점 완화 (근사치: 최신 사고일 기준, 실제 지속 운행 여부는 별도 확인 필요)",
            "중간",
        )
    return RuleResult("commercial_use", w["commercial_use_penalty"], f"{kind_text} 이력 확인됨", "높음")


def rule_panel_exchange(listing: Listing, weights: dict) -> Optional[RuleResult]:
    items = listing.performance_record.panel_exchange
    if not items:
        return None
    w = weights["risk_rules"]
    delta = w["panel_exchange_penalty_per_item"] * len(items)
    return RuleResult(
        "panel_exchange",
        delta,
        f"외판 교환 {len(items)}건 확인({', '.join(items)})",
        "중간",
    )


_FENDER_KEYWORDS = ("fender", "펜더")
_HOOD_KEYWORDS = ("hood", "후드")


def rule_fender_hood_combo(listing: Listing, weights: dict) -> Optional[RuleResult]:
    items = [i.lower() for i in listing.performance_record.panel_exchange]
    has_fender = any(any(k in i for k in _FENDER_KEYWORDS) for i in items)
    has_hood = any(any(k in i for k in _HOOD_KEYWORDS) for i in items)
    if has_fender and has_hood:
        w = weights["risk_rules"]
        return RuleResult(
            "fender_hood_combo",
            w["fender_hood_combo_penalty"],
            "펜더+후드 동시 교환 — 전면 강충격 의심 신호",
            "중간",
        )
    return None


def rule_high_value_claim(listing: Listing, weights: dict) -> Optional[RuleResult]:
    w = weights["risk_rules"]
    threshold = w["high_value_claim_threshold_krw"]
    max_amount = 0
    for c in listing.insurance_history.own_damage_claims:
        max_amount = max(max_amount, c.amount_krw or 0)
    for c in listing.insurance_history.other_party_damage_claims:
        max_amount = max(max_amount, c.amount_krw or 0)
    if max_amount >= threshold:
        return RuleResult(
            "high_value_claim",
            w["high_value_claim_penalty"],
            f"고액 단건 사고 처리 이력({max_amount:,}원) 확인",
            "높음",
        )
    return None


def rule_small_claims(listing: Listing, weights: dict) -> Optional[RuleResult]:
    w = weights["risk_rules"]
    threshold = w["small_claim_threshold_krw"]
    small_claims = [
        c for c in (listing.insurance_history.own_damage_claims + listing.insurance_history.other_party_damage_claims)
        if 0 < (c.amount_krw or 0) < threshold
    ]
    if len(small_claims) < w["small_claim_min_count"]:
        return None
    delta = min(len(small_claims) * w["small_claim_bonus_per_item"], w["small_claim_bonus_max"])
    return RuleResult(
        "small_claims",
        delta,
        f"{threshold:,}원 미만 소액 처리 {len(small_claims)}건 — 꼼꼼한 관리 이력의 프록시로 가점",
        "중간",
    )


def rule_third_party_inspection(listing: Listing, weights: dict) -> Optional[RuleResult]:
    provider = listing.performance_record.third_party_inspection.provider
    if provider and provider != "none":
        w = weights["risk_rules"]
        return RuleResult(
            "third_party_inspection",
            w["third_party_inspection_bonus"],
            f"제3자 성능 인증 존재({provider})",
            "높음",
        )
    return None


def rule_dealer_trust(listing: Listing, weights: dict, current_year: Optional[int] = None) -> Optional[RuleResult]:
    w = weights["risk_rules"]
    current_year = current_year or date.today().year
    join_year = listing.dealer.join_year
    sales = listing.dealer.total_sales_count or 0
    if join_year is None:
        return None
    years_active = current_year - join_year
    join_threshold = w["dealer_trust_join_years_threshold"]
    sales_threshold = w["dealer_trust_sales_threshold"]
    if years_active >= join_threshold and sales >= sales_threshold:
        # 단계적 완화 적용: 기준을 크게 초과할수록 가점을 소폭 늘리되 상한을 둔다.
        excess_ratio = min(years_active / join_threshold, sales / sales_threshold)
        scaled_bonus = round(w["dealer_trust_bonus"] * min(1.5, 0.7 + 0.3 * excess_ratio))
        return RuleResult(
            "dealer_trust",
            scaled_bonus,
            f"딜러 신뢰도 높음(활동 {years_active}년, 누적판매 {sales}대)",
            "중간",
        )
    return None


def rule_region_risk(listing: Listing, weights: dict, enabled: bool = True) -> Optional[RuleResult]:
    if not enabled:
        return None
    region = listing.dealer.region or listing.vehicle.region
    if region and any(risky in region for risky in weights.get("region_risk_list", [])):
        w = weights["risk_rules"]
        return RuleResult(
            "region_risk",
            w["region_risk_penalty"],
            f"딜러 지역이 리스크 지역 목록에 포함됨({region}) — 경험적 휴리스틱, 참고용",
            "낮음",
        )
    return None


def rule_premium_tire(listing: Listing, weights: dict) -> Optional[RuleResult]:
    if not listing.tire_brand:
        return None
    brand = listing.tire_brand.lower()
    if any(p.lower() in brand for p in weights.get("premium_tire_brands", [])):
        w = weights["risk_rules"]
        return RuleResult(
            "premium_tire",
            w["premium_tire_bonus"],
            f"프리미엄 타이어 브랜드 장착({listing.tire_brand}) — 참고용",
            "낮음",
        )
    return None


def rule_vision_repaint_suspicion(listing: Listing, weights: dict) -> Optional[RuleResult]:
    analysis = listing.photos.vision_analysis or {}
    if analysis.get("repaint_suspected"):
        w = weights["risk_rules"]
        return RuleResult(
            "vision_repaint_suspicion",
            w["vision_repaint_suspicion_penalty"],
            "사진 분석에서 패널 간 도장 색상/광택 불일치 플래그 감지 — 재도장 의심(보조 신호)",
            "낮음~중간",
        )
    return None


def rule_total_loss_or_flood_history(listing: Listing, weights: dict) -> Optional[RuleResult]:
    """KB차차차처럼 전손/침수 이력을 명시적으로 노출하는 소스용 규칙.

    `listing_text.claims_parsed`에 어댑터가 붙인 "전손이력있음"/"침수이력있음" 태그를 근거로 한다.
    전손/침수는 스펙 5.1 하드필터 표에 없는 항목이라 하드 배제로 승격하지 않고 큰 감점으로만 반영한다.
    """
    claims = listing.listing_text.claims_parsed
    hits = [c for c in ("전손이력있음", "침수이력있음") if c in claims]
    if not hits:
        return None
    w = weights["risk_rules"]
    return RuleResult(
        "total_loss_or_flood_history",
        w["total_loss_or_flood_penalty"],
        f"{'·'.join(hits)} 확인됨 — 전손/침수 이력은 수리 후에도 안전·전자장비 리스크가 남을 수 있음",
        "높음",
    )


def rule_accident_history_flag(listing: Listing, weights: dict) -> Optional[RuleResult]:
    """부위별 상세 없이 "사고유무"만 노출하는 소스(KB차차차)용 보조 신호.

    frame_damage 하드필터와는 별개다 — 이 배지는 어느 부위인지 알려주지 않으므로 확정적 배제 근거로
    쓰지 않고, 가벼운 감점 참고용 신호로만 반영한다.
    """
    if "사고있음" in listing.listing_text.claims_parsed:
        w = weights["risk_rules"]
        return RuleResult(
            "accident_history_flag",
            w["accident_history_flag_penalty"],
            "사고이력 있음(부위별 상세 아님, 포괄적 배지) — 성능점검기록부 원본에서 부위 직접 확인 권장",
            "낮음",
        )
    return None


# ---------------------------------------------------------------------------
# 정합성 교차검증 (5.2 하단, 별도 모듈 권장 항목)
# ---------------------------------------------------------------------------

_FRONT_PANEL_KEYWORDS = ("front", "전면", "hood", "후드", "fender", "펜더", "bumper", "범퍼")


def check_unexplained_mismatch(listing: Listing, weights: dict) -> Optional[RuleResult]:
    """고액 대물 사고 이력이 있는데 대응 부위 교환 기록이 없는 경우를 플래그."""
    w = weights["risk_rules"]
    threshold = w["high_value_claim_threshold_krw"]
    has_high_value_other_party_claim = any(
        (c.amount_krw or 0) >= threshold for c in listing.insurance_history.other_party_damage_claims
    )
    if not has_high_value_other_party_claim:
        return None

    exchanged = [i.lower() for i in listing.performance_record.panel_exchange]
    has_front_exchange = any(any(k in i for k in _FRONT_PANEL_KEYWORDS) for i in exchanged)
    if has_front_exchange:
        return None

    return RuleResult(
        "unexplained_mismatch",
        w["unexplained_mismatch_penalty"],
        "고액(500만원 이상) 대물 사고 이력이 있으나 성능기록부상 대응 부위(전면 등) 교환 기록 없음 "
        "— 이력 불일치, '모르겠으면 안 사면 된다' 원칙에 따라 제외 후보로 분류 권장",
        "높음",
    )


# ---------------------------------------------------------------------------
# 데이터 결측 경고 (하드필터 신뢰도 관련, 스펙 철학: "모르겠으면 안 사면 된다")
# ---------------------------------------------------------------------------

def detect_data_gaps(listing: Listing) -> list[str]:
    """이 매물의 정규화 데이터가 하드필터/스코어링을 신뢰할 만큼 충분한지 점검한다.

    특히 performance_record가 비어있는 것은 "프레임 손상이 없다"는 의미가 아니라
    "확인하지 못했다"는 의미일 수 있으므로, 이 경우 하드필터 결과를 맹신하지 않도록 경고한다.
    """
    gaps: list[str] = []
    pr = listing.performance_record
    if not pr.panel_exchange and not pr.frame_damage and pr.third_party_inspection.provider == "none":
        gaps.append(
            "성능기록부(외판교환/프레임손상) 데이터 미확보 — frame_damage 하드필터 결과를 신뢰할 수 없음. "
            "구매 전 반드시 성능점검기록부를 별도로 확인할 것"
        )
    ih = listing.insurance_history
    if not ih.info_unavailable_periods and ih.history_disclosed is not False:
        gaps.append("이력조회 불가기간(info_unavailable_periods) 데이터 미확보 — 관련 하드필터가 항상 통과 처리됨")
    if ih.history_disclosed is None:
        gaps.append("보험이력 공개 여부(history_disclosed) 확인 불가")
    return gaps


ALL_RISK_RULES = [
    rule_single_owner,
    rule_owner_change_penalty,
    rule_commercial_use,
    rule_panel_exchange,
    rule_fender_hood_combo,
    rule_high_value_claim,
    rule_small_claims,
    rule_third_party_inspection,
    rule_dealer_trust,
    rule_premium_tire,
    rule_vision_repaint_suspicion,
    rule_total_loss_or_flood_history,
    rule_accident_history_flag,
]
