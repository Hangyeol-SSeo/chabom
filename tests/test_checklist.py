"""scoring/checklist.py의 fail-closed 게이팅 로직을 검증한다.

핵심 원칙(사용자 요구, 2026-09-18): 핵심 항목이 FAIL이든 UNKNOWN(확인 안 됨)이든 전체 판정은
"보류"여야 한다 — 1종 오류(부실 차량을 정상으로 오판)를 절대 범해서는 안 되기 때문이다.
"""
from __future__ import annotations

from normalizer.schema import (
    Dealer,
    InsuranceHistory,
    Listing,
    PerformanceRecord,
    ThirdPartyInspection,
    Vehicle,
)
from scoring.checklist import DealerStatus, evaluate_checklist
from scoring.scorer import load_weights

WEIGHTS = load_weights()


def clean_listing(**overrides) -> Listing:
    """모든 핵심 항목이 PASS로 확인된 "합격" 매물 — 개별 게이팅 테스트의 기준점."""
    base = Listing(
        listing_id="t1",
        source="manual",
        vehicle=Vehicle(price_krw=15_000_000),
        insurance_history=InsuranceHistory(
            flood_damage=False,
            total_loss=False,
            theft=False,
            history_disclosed=True,
            info_unavailable_periods=[],
        ),
        performance_record=PerformanceRecord(
            third_party_inspection=ThirdPartyInspection(provider="user_confirmed", frame_ok=True)
        ),
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def item(result, key):
    return next(i for i in result.items if i.key == key)


def test_all_clean_proceeds():
    result = evaluate_checklist(clean_listing(), weights=WEIGHTS)
    assert result.overall == "proceed"
    assert result.hold_reasons == []


def test_frame_damage_unknown_forces_hold():
    listing = clean_listing()
    listing.performance_record.third_party_inspection.frame_ok = None
    result = evaluate_checklist(listing, weights=WEIGHTS)
    assert result.overall == "hold"
    assert item(result, "frame_damage").verdict == "unknown"


def test_frame_damage_confirmed_bad_fails():
    listing = clean_listing()
    listing.performance_record.third_party_inspection.frame_ok = False
    result = evaluate_checklist(listing, weights=WEIGHTS)
    assert result.overall == "hold"
    assert item(result, "frame_damage").verdict == "fail"


def test_flood_damage_unknown_forces_hold():
    listing = clean_listing()
    listing.insurance_history.flood_damage = None
    result = evaluate_checklist(listing, weights=WEIGHTS)
    assert result.overall == "hold"
    assert item(result, "flood_damage").verdict == "unknown"


def test_flood_damage_true_fails():
    listing = clean_listing()
    listing.insurance_history.flood_damage = True
    result = evaluate_checklist(listing, weights=WEIGHTS)
    assert result.overall == "hold"
    assert item(result, "flood_damage").verdict == "fail"


def test_total_loss_true_fails():
    listing = clean_listing()
    listing.insurance_history.total_loss = True
    result = evaluate_checklist(listing, weights=WEIGHTS)
    assert result.overall == "hold"
    assert item(result, "total_loss").verdict == "fail"


def test_history_not_disclosed_fails():
    listing = clean_listing()
    listing.insurance_history.history_disclosed = False
    result = evaluate_checklist(listing, weights=WEIGHTS)
    assert result.overall == "hold"
    assert item(result, "insurance_history").verdict == "fail"


def test_history_disclosed_unknown_forces_hold():
    listing = clean_listing()
    listing.insurance_history.history_disclosed = None
    result = evaluate_checklist(listing, weights=WEIGHTS)
    assert result.overall == "hold"
    assert item(result, "insurance_history").verdict == "unknown"


def test_encar_summary_without_coverage_confirmation_forces_hold():
    listing = clean_listing()
    listing.source = "encar"
    listing.insurance_history.coverage_verified = False
    listing.insurance_history.owner_change_count = None
    result = evaluate_checklist(listing, weights=WEIGHTS)
    assert item(result, "insurance_history").verdict == "unknown"
    assert item(result, "owner_change").verdict == "unknown"
    assert result.overall == "hold"


def test_owner_changes_are_caution_not_disqualifying():
    listing = clean_listing()
    listing.insurance_history.owner_change_count = 4
    result = evaluate_checklist(listing, weights=WEIGHTS)
    assert item(result, "owner_change").verdict == "caution"
    assert "4회" in item(result, "owner_change").detail
    assert result.overall == "proceed"


def test_panel_exchange_and_repair_are_caution_not_disqualifying():
    listing = clean_listing()
    listing.performance_record.record_available = True
    listing.performance_record.panel_exchange = ["프론트 휀더(우) · 교환"]
    listing.performance_record.panel_repairs = ["리어 도어(우) · 판금/용접"]
    result = evaluate_checklist(listing, weights=WEIGHTS)
    panel = item(result, "panel_exchange")
    assert panel.verdict == "caution"
    assert "2곳" in panel.detail and "리어 도어(우) · 판금/용접" in panel.detail
    assert result.overall == "proceed"


def test_frame_damage_still_fails_and_names_the_parts():
    listing = clean_listing()
    listing.performance_record.frame_damage = ["리어 패널 · 교환"]
    listing.performance_record.third_party_inspection.frame_ok = False
    result = evaluate_checklist(listing, weights=WEIGHTS)
    assert item(result, "frame_damage").verdict == "fail"
    assert "리어 패널 · 교환" in item(result, "frame_damage").detail
    assert result.overall == "hold"


def test_info_gap_fails_even_when_disclosed():
    from normalizer.schema import InfoUnavailablePeriod

    listing = clean_listing()
    listing.insurance_history.info_unavailable_periods = [
        InfoUnavailablePeriod(start="2015-01-01", end="2019-06-01")
    ]
    result = evaluate_checklist(listing, weights=WEIGHTS)
    assert result.overall == "hold"
    assert item(result, "insurance_history").verdict == "fail"


def test_theft_history_fails():
    listing = clean_listing()
    listing.insurance_history.theft = True
    result = evaluate_checklist(listing, weights=WEIGHTS)
    assert result.overall == "hold"
    assert item(result, "theft_history").verdict == "fail"


def test_theft_history_unknown_forces_hold():
    listing = clean_listing()
    listing.insurance_history.theft = None
    result = evaluate_checklist(listing, weights=WEIGHTS)
    assert result.overall == "hold"
    assert item(result, "theft_history").verdict == "unknown"


def test_rental_transfer_forces_hold():
    listing = clean_listing()
    listing.vehicle.listing_type = "rental_transfer"
    result = evaluate_checklist(listing, weights=WEIGHTS)
    assert result.overall == "hold"
    assert item(result, "listing_type").verdict == "fail"


def test_unexplained_mismatch_fails():
    from normalizer.schema import OtherPartyDamageClaim

    listing = clean_listing()
    threshold = WEIGHTS["risk_rules"]["high_value_claim_threshold_krw"]
    listing.insurance_history.other_party_damage_claims = [
        OtherPartyDamageClaim(date=None, amount_krw=threshold + 1_000_000)
    ]
    # 전면 부위 교환 기록 없음 -> 불일치
    result = evaluate_checklist(listing, weights=WEIGHTS)
    assert result.overall == "hold"
    assert item(result, "claim_repair_consistency").verdict == "fail"


# ---------------------------------------------------------------------------
# 딜러 블랙리스트 게이팅
# ---------------------------------------------------------------------------

def test_dealer_blacklisted_forces_hold_even_if_car_is_clean():
    dealer_status = DealerStatus(source="bobaedream", dealer_key="abc", known=True, blacklisted=True, reason="침수 은폐 이력")
    result = evaluate_checklist(clean_listing(), dealer_status=dealer_status, weights=WEIGHTS)
    assert result.overall == "hold"
    assert item(result, "dealer_blacklist").verdict == "fail"


def test_dealer_unknown_identity_does_not_force_hold():
    result = evaluate_checklist(clean_listing(), dealer_status=DealerStatus(known=False), weights=WEIGHTS)
    assert result.overall == "proceed"
    dealer_item = item(result, "dealer_blacklist")
    assert dealer_item.verdict == "unknown"
    assert dealer_item.critical is False


def test_dealer_known_not_blacklisted_passes():
    dealer_status = DealerStatus(source="bobaedream", dealer_key="abc", known=True, blacklisted=False)
    result = evaluate_checklist(clean_listing(), dealer_status=dealer_status, weights=WEIGHTS)
    assert result.overall == "proceed"
    assert item(result, "dealer_blacklist").verdict == "pass"
