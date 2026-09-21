"""카히스토리(보험개발원) 사고이력 데이터를 매물 사이트 경유가 아닌 별도 API로 받아오기 위한
연동 지점 (스펙 4.2) — 스텁.

carhistory.or.kr 자체는 건당 유료 조회 + 본인/차량 인증이 필요해 직접 스크레이핑 대상으로
적합하지 않다고 판단했다(스펙 4.2). 대안으로 CODEF(developer.codef.io)가 "사고이력조회 -
카히스토리" 상품을 유료 API로 제공한다.

이 모듈은 의도적으로 미구현 상태다 — API 키 발급, 과금 구조 확인, 이용약관 동의가 선행되어야
하며 이는 사용자의 계정/계약이 필요한 영역이라 이 세션에서 대신 진행할 수 없다.
크롤러만으로 insurance_history/performance_record를 충분히 확보하지 못하는 사이트(엔카/케이카/
KB차차차 등, crawler/compliance.py 참고)를 보강하려면 이 클라이언트를 완성해 연결하는 것을
권장한다.
"""
from __future__ import annotations

from normalizer.schema import InsuranceHistory


class CodefCarHistoryClient:
    def __init__(self, api_key: str, api_secret: str):
        raise NotImplementedError(
            "CODEF 연동은 이번 구현 범위에 포함되지 않았습니다. "
            "developer.codef.io에서 계정/상품 계약 및 과금 구조 확인 후 직접 구현하세요."
        )

    def fetch_insurance_history(self, vehicle_number: str, owner_name: str, owner_birth: str) -> InsuranceHistory:
        raise NotImplementedError
