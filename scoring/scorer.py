"""리스크/가성비 스코어 계산 및 근거 문자열 생성 (스펙 5.2~5.4)."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import yaml

from normalizer.schema import Listing, parse_date
from scoring.rules import (
    ALL_RISK_RULES,
    HardFilterHit,
    RuleResult,
    check_hard_filters,
    check_unexplained_mismatch,
    detect_data_gaps,
    rule_region_risk,
)

DEFAULT_WEIGHTS_PATH = Path(__file__).with_name("weights.yaml")


def load_weights(path: Optional[Path] = None) -> dict:
    path = path or DEFAULT_WEIGHTS_PATH
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class RiskScoreResult:
    score: int
    raw_score: float
    rule_hits: list[RuleResult] = field(default_factory=list)
    unexplained_mismatch: bool = False


@dataclass
class ValueScoreResult:
    score: Optional[float]
    depreciation_rate: Optional[float]
    segment_avg_depreciation_rate: Optional[float]
    annual_mileage_km: Optional[int]
    high_annual_mileage_hint: bool = False


@dataclass
class EvaluatedListing:
    listing: Listing
    excluded: bool
    hard_filter_hits: list[HardFilterHit]
    risk: RiskScoreResult
    value: ValueScoreResult
    explanation: str
    data_gaps: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 가성비 스코어 (5.3)
# ---------------------------------------------------------------------------

def _estimate_new_car_price(listing: Listing, all_listings: list[Listing]) -> Optional[int]:
    if listing.vehicle.new_car_price_krw:
        return listing.vehicle.new_car_price_krw
    candidates = [
        other.vehicle.price_krw
        for other in all_listings
        if other.listing_id != listing.listing_id
        and other.vehicle.model == listing.vehicle.model
        and other.vehicle.trim == listing.vehicle.trim
        and other.vehicle.model_year == listing.vehicle.model_year
        and other.vehicle.price_krw
    ]
    if candidates:
        return int(statistics.median(candidates))
    return None


def _depreciation_rate(listing: Listing, all_listings: list[Listing]) -> Optional[float]:
    price = listing.vehicle.price_krw
    new_price = _estimate_new_car_price(listing, all_listings)
    if not price or not new_price:
        return None
    return 1 - (price / new_price)


def _annual_mileage_km(listing: Listing, today: Optional[date] = None) -> Optional[int]:
    reg_date = parse_date(listing.vehicle.first_registration_date)
    mileage = listing.vehicle.mileage_km
    if not reg_date or not mileage:
        return None
    today = today or date.today()
    days = (today - reg_date).days
    if days <= 0:
        return None
    return round(mileage / (days / 365.25))


def compute_value_score(listing: Listing, all_listings: list[Listing], weights: dict) -> ValueScoreResult:
    rate = _depreciation_rate(listing, all_listings)
    annual_mileage = _annual_mileage_km(listing)
    high_mileage_threshold = weights["mileage"]["high_annual_mileage_km"]
    high_mileage_hint = bool(annual_mileage and annual_mileage >= high_mileage_threshold)

    if rate is None:
        return ValueScoreResult(
            score=None,
            depreciation_rate=None,
            segment_avg_depreciation_rate=None,
            annual_mileage_km=annual_mileage,
            high_annual_mileage_hint=high_mileage_hint,
        )

    segment_rates = []
    for other in all_listings:
        if other.vehicle.body_type != listing.vehicle.body_type:
            continue
        r = _depreciation_rate(other, all_listings)
        if r is not None:
            segment_rates.append(r)
    segment_avg = statistics.mean(segment_rates) if segment_rates else rate

    diff = rate - segment_avg
    # 세그먼트 평균 대비 감가율이 클수록(가성비가 좋을수록) 높은 점수. 스케일은 임의 상수이며 weights.yaml로 옮겨 튜닝 가능.
    score = max(0.0, min(100.0, 50 + diff * 200))

    return ValueScoreResult(
        score=round(score, 1),
        depreciation_rate=round(rate, 4),
        segment_avg_depreciation_rate=round(segment_avg, 4),
        annual_mileage_km=annual_mileage,
        high_annual_mileage_hint=high_mileage_hint,
    )


# ---------------------------------------------------------------------------
# 리스크 스코어 (5.2)
# ---------------------------------------------------------------------------

def compute_risk_score(listing: Listing, weights: dict, region_risk_enabled: bool = True) -> RiskScoreResult:
    hits: list[RuleResult] = []
    for rule_fn in ALL_RISK_RULES:
        result = rule_fn(listing, weights)
        if result:
            hits.append(result)

    region_result = rule_region_risk(listing, weights, enabled=region_risk_enabled)
    if region_result:
        hits.append(region_result)

    mismatch = check_unexplained_mismatch(listing, weights)
    if mismatch:
        hits.append(mismatch)

    raw = weights["base_score"] + sum(h.delta for h in hits)
    clamped = int(round(max(0, min(100, raw))))
    return RiskScoreResult(score=clamped, raw_score=raw, rule_hits=hits, unexplained_mismatch=mismatch is not None)


# ---------------------------------------------------------------------------
# 종합 평가
# ---------------------------------------------------------------------------

def _explanation(hard_hits: list[HardFilterHit], risk: RiskScoreResult) -> str:
    if hard_hits:
        return "제외 대상: " + "; ".join(h.reason for h in hard_hits)
    if not risk.rule_hits:
        return "특이 신호 없음(기본 점수 유지)"
    ordered = sorted(risk.rule_hits, key=lambda r: -abs(r.delta))
    return ", ".join(r.reason for r in ordered)


def evaluate_listing(
    listing: Listing,
    all_listings: list[Listing],
    weights: dict,
    advanced_mode: bool = False,
    region_risk_enabled: bool = True,
) -> EvaluatedListing:
    hard_hits = check_hard_filters(listing, weights)
    excluded = bool(hard_hits) and not advanced_mode

    risk = compute_risk_score(listing, weights, region_risk_enabled=region_risk_enabled)
    value = compute_value_score(listing, all_listings, weights)
    explanation = _explanation(hard_hits, risk)
    data_gaps = detect_data_gaps(listing)

    return EvaluatedListing(
        listing=listing,
        excluded=excluded,
        hard_filter_hits=hard_hits,
        risk=risk,
        value=value,
        explanation=explanation,
        data_gaps=data_gaps,
    )


def evaluate_all(
    listings: list[Listing],
    weights: dict,
    advanced_mode: bool = False,
    region_risk_enabled: bool = True,
) -> list[EvaluatedListing]:
    return [
        evaluate_listing(l, listings, weights, advanced_mode=advanced_mode, region_risk_enabled=region_risk_enabled)
        for l in listings
    ]


# ---------------------------------------------------------------------------
# 최종 랭킹 (5.4)
# ---------------------------------------------------------------------------

def rank_listings(
    evaluated: list[EvaluatedListing],
    weights: dict,
    mode: str = "risk",
) -> list[EvaluatedListing]:
    candidates = [e for e in evaluated if not e.excluded]

    if mode == "value":
        threshold = weights["final_ranking"]["value_mode_risk_threshold"]
        candidates = [e for e in candidates if e.risk.score >= threshold]
        return sorted(candidates, key=lambda e: -(e.value.score or -1))

    return sorted(candidates, key=lambda e: (-e.risk.score, -(e.value.score or -1)))
