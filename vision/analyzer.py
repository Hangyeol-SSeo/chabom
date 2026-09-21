"""사진 기반 2차 분석 모듈 (스펙 7장) — 스텁.

의도적으로 비활성 상태로 둔다. 스펙 8장 우선순위상 파이프라인이 안정화된 이후 마지막 단계로
추가할 항목이며, 이 세션에서는 구현하지 않았다.

구현 시 권장 방식(스펙 7장):
  - 하드필터·1차 스코어링을 통과한 상위 후보군에 한해서만 호출(비용 절감).
  - 비전 지원 모델(API)에 사진 URL 목록을 전달해 아래 항목을 판정:
      * 패널 간 도장 색상/광택 불일치(재도장 의심)
      * 명백한 외관 손상/부식
      * 계기판 사진 주행거리와 매물 정보 대조(OCR)
      * 타이어 브랜드/마모 상태
      * 실내 마모도
  - 프레임(뼈대) 손상 여부는 사진으로 판정하지 않는다(스펙 7장 — 하드필터 근거로 승격 금지).
  - 결과는 `normalizer.schema.Photos.vision_analysis` 딕셔너리에 담아
    `scoring/rules.py::rule_vision_repaint_suspicion` 등 보조 신호로만 반영한다.
  - 이미지는 개인적 분석 목적의 일시 캐시 후 폐기하며 재배포하지 않는다(스펙 4.3).
"""
from __future__ import annotations

from normalizer.schema import Listing


def analyze_listing_photos(listing: Listing) -> dict:
    """미구현 스텁. 호출 시 명시적으로 알린다 (조용히 빈 결과를 반환하지 않는다)."""
    raise NotImplementedError(
        "vision_analysis는 이번 구현 범위에 포함되지 않았습니다. "
        "스펙 7장/8장 참고 — 파이프라인 안정화 이후 비전 API 연동을 별도로 구현하세요."
    )
