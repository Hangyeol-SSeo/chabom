"""평가 결과(EvaluatedListing)를 JSON 직렬화 가능한 딕셔너리로 변환.

CLI(cli/main.py)의 JSON 저장과 로컬 검색 서버(server.py)의 API 응답이 동일한 형태를
쓰도록 공통화했다 — 웹앱(web/index.html)이 두 경로 어느 쪽에서 와도 같은 필드를 기대한다.
"""
from __future__ import annotations

import dataclasses
from typing import Optional

from scoring.scorer import EvaluatedListing


def evaluated_to_row(e: EvaluatedListing, batch_meta: Optional[dict] = None) -> dict:
    row = {
        "listing": e.listing.to_dict(),
        "excluded": e.excluded,
        "risk_score": e.risk.score,
        "value_score": e.value.score,
        "explanation": e.explanation,
        "data_gaps": e.data_gaps,
        "risk_rule_hits": [dataclasses.asdict(r) for r in e.risk.rule_hits],
        "hard_filter_hits": [dataclasses.asdict(h) for h in e.hard_filter_hits],
        "unexplained_mismatch": e.risk.unexplained_mismatch,
        "value_detail": {
            "depreciation_rate": e.value.depreciation_rate,
            "segment_avg_depreciation_rate": e.value.segment_avg_depreciation_rate,
            "annual_mileage_km": e.value.annual_mileage_km,
            "high_annual_mileage_hint": e.value.high_annual_mileage_hint,
        },
    }
    if batch_meta:
        row.update(batch_meta)
    return row


def evaluated_to_rows(evaluated: list[EvaluatedListing], batch_meta: Optional[dict] = None) -> list[dict]:
    return [evaluated_to_row(e, batch_meta) for e in evaluated]
