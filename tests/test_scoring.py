from __future__ import annotations

import json
from pathlib import Path

import pytest

from normalizer.schema import (
    DamageClaim,
    Dealer,
    InsuranceHistory,
    Listing,
    OtherPartyDamageClaim,
    PerformanceRecord,
    ThirdPartyInspection,
    UsageHistory,
    Vehicle,
)
from scoring import rules
from scoring.scorer import evaluate_all, evaluate_listing, load_weights, rank_listings

WEIGHTS = load_weights()
SAMPLE_PATH = Path(__file__).with_name("sample_listings.json")


def make_listing(**overrides) -> Listing:
    base = Listing(listing_id="t1", source="sample", vehicle=Vehicle(price_krw=10_000_000))
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def load_sample_listings() -> list[Listing]:
    raw = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    return [Listing.from_dict(item) for item in raw]


# ---------------------------------------------------------------------------
# 하드 필터
# ---------------------------------------------------------------------------

def test_hard_filter_frame_damage_excludes():
    listing = make_listing(performance_record=PerformanceRecord(frame_damage=["front_side_member"]))
    hits = rules.check_hard_filters(listing, WEIGHTS)
    assert any(h.rule_id == "frame_damage" for h in hits)


def test_hard_filter_history_not_disclosed_excludes():
    listing = make_listing(insurance_history=InsuranceHistory(history_disclosed=False))
    hits = rules.check_hard_filters(listing, WEIGHTS)
    assert any(h.rule_id == "history_not_disclosed" for h in hits)


def test_hard_filter_info_unavailable_period_excludes():
    from normalizer.schema import InfoUnavailablePeriod

    listing = make_listing(
        insurance_history=InsuranceHistory(
            info_unavailable_periods=[InfoUnavailablePeriod(start="2015-01-01", end="2019-06-01")]
        )
    )
    hits = rules.check_hard_filters(listing, WEIGHTS)
    assert any(h.rule_id == "info_unavailable_too_long" for h in hits)


def test_hard_filter_clean_listing_passes():
    listing = make_listing()
    hits = rules.check_hard_filters(listing, WEIGHTS)
    assert hits == []


# ---------------------------------------------------------------------------
# 리스크 규칙
# ---------------------------------------------------------------------------

def test_single_owner_bonus():
    listing = make_listing(insurance_history=InsuranceHistory(owner_change_count=1))
    result = rules.rule_single_owner(listing, WEIGHTS)
    assert result is not None
    assert result.delta == WEIGHTS["risk_rules"]["single_owner_bonus"]


def test_owner_change_penalty_scales_with_count():
    listing = make_listing(insurance_history=InsuranceHistory(owner_change_count=4))
    result = rules.rule_owner_change_penalty(listing, WEIGHTS)
    assert result is not None
    assert result.delta == WEIGHTS["risk_rules"]["owner_change_penalty"] * 4


def test_owner_change_penalty_mitigated_for_light_car():
    listing = make_listing(
        vehicle=Vehicle(model="모닝", price_krw=5_000_000),
        insurance_history=InsuranceHistory(owner_change_count=3),
    )
    result = rules.rule_owner_change_penalty(listing, WEIGHTS)
    assert result.delta == WEIGHTS["risk_rules"]["owner_change_penalty_light_car"] * 3


def test_commercial_use_penalty_without_mitigation():
    listing = make_listing(insurance_history=InsuranceHistory(usage_history=UsageHistory(taxi_used=True)))
    result = rules.rule_commercial_use(listing, WEIGHTS)
    assert result.rule_id == "commercial_use"
    assert result.delta == WEIGHTS["risk_rules"]["commercial_use_penalty"]


def test_commercial_use_penalty_mitigated_after_years():
    from datetime import date

    listing = make_listing(
        insurance_history=InsuranceHistory(
            usage_history=UsageHistory(taxi_used=True),
            own_damage_claims=[DamageClaim(date="2018-01-01", amount_krw=100_000)],
        )
    )
    result = rules.rule_commercial_use(listing, WEIGHTS, today=date(2025, 1, 1))
    assert result.rule_id == "commercial_use_mitigated"
    assert result.delta == WEIGHTS["risk_rules"]["commercial_use_penalty_mitigated"]


def test_fender_hood_combo_extra_penalty():
    listing = make_listing(
        performance_record=PerformanceRecord(panel_exchange=["hood", "front_fender_l"])
    )
    result = rules.rule_fender_hood_combo(listing, WEIGHTS)
    assert result is not None
    assert result.delta == WEIGHTS["risk_rules"]["fender_hood_combo_penalty"]


def test_high_value_claim_penalty():
    listing = make_listing(
        insurance_history=InsuranceHistory(own_damage_claims=[DamageClaim(date="2022-01-01", amount_krw=6_000_000)])
    )
    result = rules.rule_high_value_claim(listing, WEIGHTS)
    assert result is not None
    assert result.delta == WEIGHTS["risk_rules"]["high_value_claim_penalty"]


def test_small_claims_bonus_capped():
    claims = [DamageClaim(date="2022-01-01", amount_krw=500_000) for _ in range(5)]
    listing = make_listing(insurance_history=InsuranceHistory(own_damage_claims=claims))
    result = rules.rule_small_claims(listing, WEIGHTS)
    assert result is not None
    assert result.delta == WEIGHTS["risk_rules"]["small_claim_bonus_max"]


def test_third_party_inspection_bonus():
    listing = make_listing(
        performance_record=PerformanceRecord(third_party_inspection=ThirdPartyInspection(provider="encar_diagnosis"))
    )
    result = rules.rule_third_party_inspection(listing, WEIGHTS)
    assert result is not None
    assert result.delta == WEIGHTS["risk_rules"]["third_party_inspection_bonus"]


def test_region_risk_penalty_when_enabled():
    listing = make_listing(dealer=Dealer(region="인천 남동구"))
    result = rules.rule_region_risk(listing, WEIGHTS, enabled=True)
    assert result is not None
    assert result.delta == WEIGHTS["risk_rules"]["region_risk_penalty"]


def test_region_risk_penalty_disabled_returns_none():
    listing = make_listing(dealer=Dealer(region="인천 남동구"))
    result = rules.rule_region_risk(listing, WEIGHTS, enabled=False)
    assert result is None


def test_total_loss_or_flood_history_penalty():
    from normalizer.schema import ListingText

    listing = make_listing(listing_text=ListingText(claims_parsed=["침수이력있음"]))
    result = rules.rule_total_loss_or_flood_history(listing, WEIGHTS)
    assert result is not None
    assert result.delta == WEIGHTS["risk_rules"]["total_loss_or_flood_penalty"]


def test_total_loss_or_flood_history_none_when_absent():
    from normalizer.schema import ListingText

    listing = make_listing(listing_text=ListingText(claims_parsed=["사고없음"]))
    assert rules.rule_total_loss_or_flood_history(listing, WEIGHTS) is None


def test_accident_history_flag_penalty():
    from normalizer.schema import ListingText

    listing = make_listing(listing_text=ListingText(claims_parsed=["사고있음"]))
    result = rules.rule_accident_history_flag(listing, WEIGHTS)
    assert result is not None
    assert result.delta == WEIGHTS["risk_rules"]["accident_history_flag_penalty"]


def test_unexplained_mismatch_flags_when_no_front_exchange():
    listing = make_listing(
        insurance_history=InsuranceHistory(
            other_party_damage_claims=[OtherPartyDamageClaim(date="2021-01-01", amount_krw=6_000_000)]
        ),
        performance_record=PerformanceRecord(panel_exchange=["trunk"]),
    )
    result = rules.check_unexplained_mismatch(listing, WEIGHTS)
    assert result is not None
    assert result.delta == WEIGHTS["risk_rules"]["unexplained_mismatch_penalty"]


def test_unexplained_mismatch_not_flagged_when_front_exchanged():
    listing = make_listing(
        insurance_history=InsuranceHistory(
            other_party_damage_claims=[OtherPartyDamageClaim(date="2021-01-01", amount_krw=6_000_000)]
        ),
        performance_record=PerformanceRecord(panel_exchange=["front_bumper"]),
    )
    result = rules.check_unexplained_mismatch(listing, WEIGHTS)
    assert result is None


# ---------------------------------------------------------------------------
# 데이터 결측 경고
# ---------------------------------------------------------------------------

def test_detect_data_gaps_flags_missing_performance_record():
    listing = make_listing()
    gaps = rules.detect_data_gaps(listing)
    assert any("성능기록부" in g for g in gaps)


def test_detect_data_gaps_clean_when_third_party_inspection_present():
    listing = make_listing(
        performance_record=PerformanceRecord(third_party_inspection=ThirdPartyInspection(provider="encar_diagnosis")),
        insurance_history=InsuranceHistory(history_disclosed=True),
    )
    gaps = rules.detect_data_gaps(listing)
    assert not any("성능기록부" in g for g in gaps)


# ---------------------------------------------------------------------------
# 종합 평가 / 샘플 데이터 (스펙 8.3: 수동 입력 JSON으로 단위 테스트)
# ---------------------------------------------------------------------------

def test_sample_listings_evaluate_as_expected():
    listings = load_sample_listings()
    evaluated = evaluate_all(listings, WEIGHTS)
    by_id = {e.listing.listing_id: e for e in evaluated}

    assert by_id["sample-frame-damage-002"].excluded is True
    assert by_id["sample-history-hidden-004"].excluded is True

    clean = by_id["sample-clean-001"]
    assert clean.excluded is False
    assert clean.risk.score > 50

    mismatch = by_id["sample-mismatch-003"]
    assert mismatch.risk.unexplained_mismatch is True


def test_advanced_mode_does_not_exclude():
    listings = load_sample_listings()
    evaluated = evaluate_all(listings, WEIGHTS, advanced_mode=True)
    assert all(e.excluded is False for e in evaluated)


def test_rank_listings_risk_mode_sorted_descending():
    listings = load_sample_listings()
    evaluated = evaluate_all(listings, WEIGHTS)
    ranked = rank_listings(evaluated, WEIGHTS, mode="risk")
    scores = [e.risk.score for e in ranked]
    assert scores == sorted(scores, reverse=True)
    assert all(not e.excluded for e in ranked)


def test_rank_listings_value_mode_filters_by_risk_threshold():
    listings = load_sample_listings()
    evaluated = evaluate_all(listings, WEIGHTS)
    ranked = rank_listings(evaluated, WEIGHTS, mode="value")
    threshold = WEIGHTS["final_ranking"]["value_mode_risk_threshold"]
    assert all(e.risk.score >= threshold for e in ranked)
