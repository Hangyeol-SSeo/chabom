"""매물 1건을 체크리스트로 검증한다 (2026-09-18 신설, 기존 0~100 점수 방식을 대체).

**배경**: 사용자가 명시적으로 요구한 원칙 — "2종 오류(정상 차량을 부실로 오판)는 감수할 수
있지만, 1종 오류(부실 차량을 정상으로 오판)는 절대 안 된다. 차는 딱 한 번만 잘 사면 된다."
기존의 0~100 합산 리스크 점수는 핵심 항목(프레임손상 등)이 "확인 안 됨" 상태여도 다른 가점
항목들 때문에 "65점"처럼 그럴듯한 숫자가 나올 수 있어 이 원칙과 정면으로 배치된다.

그래서 이 모듈은 점수를 합산하지 않는다. 대신:
- 핵심 항목(critical=True)은 PASS/FAIL/UNKNOWN 셋 중 하나이며, **FAIL은 물론 UNKNOWN(확인 안
  됨)도 FAIL과 동일하게 "구매 보류"를 강제한다** — "모르겠으면 안 사면 된다"를 점수가 아니라
  판정 로직 자체에 박아 넣었다.
- 핵심이 아닌 항목(참고 정보 — 가격, 주행거리, 명의변경 횟수 등)은 게이팅하지 않고 그냥 보여만
  준다.
- 딜러 블랙리스트(storage/dealers.py) 매치는 다른 모든 항목과 무관하게 최우선으로 게이팅한다.

기존 scoring/rules.py의 정합성 교차검증(check_unexplained_mismatch)은 그대로 재사용한다 — 이건
점수가 아니라 이미 불리언에 가까운 판정이라 체크리스트 모델과 잘 맞는다. scoring/scorer.py(0~100
점수 계산)는 삭제하지 않고 그대로 뒀다 — CLI의 배치용 score/crawl 명령이 계속 참조한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from normalizer.schema import Listing
from scoring.rules import check_unexplained_mismatch
from scoring.scorer import load_weights

Verdict = str  # "pass" | "fail" | "unknown"


@dataclass
class ChecklistItem:
    key: str
    label: str
    verdict: Verdict
    detail: str
    critical: bool  # True면 fail/unknown일 때 전체 판정을 "보류"로 강제한다


@dataclass
class DealerStatus:
    source: str = ""
    dealer_key: str = ""
    known: bool = False  # 이 매물에서 딜러 식별 정보(dealer_key)를 확보했는지
    blacklisted: bool = False
    reason: str = ""
    blacklisted_at: str = ""
    phone_matches: list[dict] = field(default_factory=list)  # 동일 전화번호를 쓰는 다른(사이트,키) 딜러 — 참고용


@dataclass
class ChecklistResult:
    items: list[ChecklistItem] = field(default_factory=list)
    dealer_status: Optional[DealerStatus] = None
    overall: str = "hold"  # "proceed" | "hold"
    hold_reasons: list[str] = field(default_factory=list)


def _dealer_item(dealer_status: Optional[DealerStatus]) -> ChecklistItem:
    if dealer_status is None or not dealer_status.known:
        return ChecklistItem(
            "dealer_blacklist", "딜러 블랙리스트 대조", "unknown",
            "이 매물에서 딜러를 식별할 정보를 확보하지 못해 블랙리스트와 대조할 수 없습니다",
            critical=False,  # 식별 정보가 아예 없는 것 자체를 차량 결함 신호로 취급하진 않는다
        )
    if dealer_status.blacklisted:
        return ChecklistItem(
            "dealer_blacklist", "딜러 블랙리스트 대조", "fail",
            f"이전에 제외 등록된 딜러입니다 — 사유: {dealer_status.reason or '(사유 미기재)'}",
            critical=True,
        )
    detail = "제외 등록된 딜러가 아닙니다"
    if dealer_status.phone_matches:
        others = ", ".join(f"{m['source']}/{m['dealer_key']}" for m in dealer_status.phone_matches)
        detail += f" — 다만 동일 연락처를 쓰는 제외 딜러 이력이 있어 참고 확인 권장({others})"
    return ChecklistItem("dealer_blacklist", "딜러 블랙리스트 대조", "pass", detail, critical=True)


def _frame_damage_item(listing: Listing) -> ChecklistItem:
    frame_ok = listing.performance_record.third_party_inspection.frame_ok
    if frame_ok is None:
        return ChecklistItem(
            "frame_damage", "프레임(뼈대) 손상", "unknown",
            "성능점검기록부 등으로 확인되지 않았습니다 — 원본을 직접 확인하기 전까지 구매 보류 권장",
            critical=True,
        )
    if frame_ok is False:
        return ChecklistItem(
            "frame_damage", "프레임(뼈대) 손상", "fail",
            "손상 이력이 확인되었습니다 — 원칙적으로 배제 대상",
            critical=True,
        )
    return ChecklistItem("frame_damage", "프레임(뼈대) 손상", "pass", "무손상으로 확인되었습니다", critical=True)


def _tri_state_bad_if_true(listing_value: Optional[bool], label_key: str, label: str, bad_word: str, ok_word: str) -> ChecklistItem:
    if listing_value is None:
        return ChecklistItem(
            label_key, label, "unknown",
            f"{label} 여부가 확인되지 않았습니다 — 확인 전까지 구매 보류 권장",
            critical=True,
        )
    if listing_value is True:
        return ChecklistItem(label_key, label, "fail", f"{bad_word} 확인되었습니다", critical=True)
    return ChecklistItem(label_key, label, "pass", f"{ok_word} 확인되었습니다", critical=True)


def _insurance_history_item(listing: Listing) -> ChecklistItem:
    ih = listing.insurance_history
    if ih.history_disclosed is None:
        return ChecklistItem(
            "insurance_history", "보험이력 조회", "unknown",
            "보험이력을 조회했는지 자체가 확인되지 않았습니다 — 카히스토리 등으로 직접 조회 전까지 보류 권장",
            critical=True,
        )
    if ih.history_disclosed is False:
        return ChecklistItem(
            "insurance_history", "보험이력 조회", "fail",
            "판매자/딜러가 조회 가능한 이력 정보를 비공개 처리했습니다 — 그 자체로 비정상 신호",
            critical=True,
        )
    if ih.info_unavailable_periods:
        total_days = sum(
            (p.end and p.start and _days_between(p.start, p.end)) or 0 for p in ih.info_unavailable_periods
        )
        gap_length = f"약 {total_days}일" if total_days else "기간 미확인"
        return ChecklistItem(
            "insurance_history", "보험이력 조회", "fail",
            f"이력 조회는 가능하나 정보 공백 기간이 있습니다({gap_length}) — 자차보험 미가입 기간 동안의 "
            "사고 이력이 누락됐을 수 있습니다",
            critical=True,
        )
    if not ih.coverage_verified and listing.source == "encar":
        return ChecklistItem(
            "insurance_history", "보험이력 조회", "unknown",
            "차량이력 요약만으로는 자차 보험 미가입 기간이 없다고 확인할 수 없습니다 — 상세 이력 확인 필요",
            critical=True,
        )
    return ChecklistItem("insurance_history", "보험이력 조회", "pass", "이력 조회 가능, 공백 기간 없음", critical=True)


def _days_between(start: str, end: str) -> int:
    from normalizer.schema import parse_date
    s, e = parse_date(start), parse_date(end)
    return (e - s).days if s and e and e > s else 0


def _mismatch_item(listing: Listing, weights: dict) -> ChecklistItem:
    result = check_unexplained_mismatch(listing, weights)
    if result is None:
        return ChecklistItem(
            "claim_repair_consistency", "사고이력-수리기록 정합성", "pass",
            "고액 대물사고 이력과 성능기록부 수리 내역 사이에 확인된 불일치가 없습니다",
            critical=True,
        )
    return ChecklistItem("claim_repair_consistency", "사고이력-수리기록 정합성", "fail", result.reason, critical=True)




def _informational_items(listing: Listing) -> list[ChecklistItem]:
    items: list[ChecklistItem] = []
    v, ih, pr = listing.vehicle, listing.insurance_history, listing.performance_record

    if v.listing_type == "rental_transfer":
        items.append(ChecklistItem(
            "listing_type", "매물 유형", "fail",
            "일반 중고차 매매가 아니라 장기렌트 승계 계약입니다 — 소유권이 아닌 월 렌트료 납부 의무를 이어받는 구조",
            critical=True,  # 사용자가 일반 매매를 찾고 있다면 이것도 사실상 치명적 — 게이팅
        ))

    if ih.owner_change_count is None:
        items.append(ChecklistItem("owner_change", "명의변경 이력", "unknown", "소유자 변경 횟수와 날짜가 확인되지 않았습니다", critical=False))
    else:
        items.append(ChecklistItem(
            "owner_change", "명의변경 이력", "pass" if ih.owner_change_count <= 1 else "fail",
            f"명의변경 {ih.owner_change_count}회" + ("(단독 소유 추정)" if ih.owner_change_count <= 1 else ""),
            critical=False,
        ))

    if ih.usage_history.any_commercial_use():
        kinds = ", ".join(k for k, used in [("렌트", ih.usage_history.rental_used), ("택시", ih.usage_history.taxi_used), ("영업용", ih.usage_history.business_used)] if used)
        items.append(ChecklistItem("commercial_use", "영업용 이력", "fail", f"{kinds} 이력이 확인되었습니다", critical=False))

    if pr.panel_exchange:
        items.append(ChecklistItem(
            "panel_exchange", "외판(패널) 교환", "fail",
            f"교환 이력 확인: {', '.join(pr.panel_exchange)} — 프레임 손상은 아니지만 사고 이력의 방증일 수 있음",
            critical=False,
        ))

    if listing.tire_brand:
        items.append(ChecklistItem("tire_brand", "타이어", "pass", f"장착 브랜드: {listing.tire_brand}", critical=False))

    return items


def evaluate_checklist(
    listing: Listing,
    dealer_status: Optional[DealerStatus] = None,
    weights: Optional[dict] = None,
) -> ChecklistResult:
    weights = weights or load_weights()

    items = [
        _dealer_item(dealer_status),
        _frame_damage_item(listing),
        _tri_state_bad_if_true(listing.insurance_history.flood_damage, "flood_damage", "침수 이력", "침수 이력이", "무침수가"),
        _tri_state_bad_if_true(listing.insurance_history.total_loss, "total_loss", "전손 이력", "전손 이력이", "전손 이력 없음이"),
        _tri_state_bad_if_true(listing.insurance_history.theft, "theft_history", "도난 이력", "도난 이력이", "도난 이력 없음이"),
        _insurance_history_item(listing),
        _mismatch_item(listing, weights),
    ]
    items.extend(_informational_items(listing))

    hold_reasons = [
        f"{it.label}: {it.detail}"
        for it in items
        if it.critical and it.verdict in ("fail", "unknown")
    ]
    overall = "hold" if hold_reasons else "proceed"

    return ChecklistResult(items=items, dealer_status=dealer_status, overall=overall, hold_reasons=hold_reasons)
