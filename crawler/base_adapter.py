"""사이트별 어댑터 공통 인터페이스 (스펙 3.5, 7장 어댑터 패턴)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional

from normalizer.schema import Listing


@dataclass
class SearchParams:
    """사용자 설정 파라미터(스펙 6장)의 크롤링 관련 부분."""

    min_price_krw: Optional[int] = None
    max_price_krw: Optional[int] = None
    body_types: Optional[list[str]] = None
    transmission: Optional[str] = None  # manual | automatic | None(무관)
    fuel_type: Optional[str] = None
    # 2026-09-17 추가: 사용자가 웹앱에서 설정한 검색 조건을 목록 조회 URL 자체에 실어(서버단
    # 필터링) 불필요한 매물을 애초에 받아오지 않기 위한 필드. 이전에는 min/max_price_krw와
    # transmission/fuel_type도 사후(local) 필터링에만 쓰였으나, 이제 각 어댑터의 목록 URL
    # 빌더가 이 필드들까지 포함해 실제 검색 조건으로 사이트에 전달한다.
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    mileage_min_km: Optional[int] = None
    mileage_max_km: Optional[int] = None
    max_pages: int = 3
    # 어댑터별 고유 파라미터(예: 보배드림의 gubun=국산/수입 구분)를 위한 확장 지점.
    extra: dict = None

    def __post_init__(self) -> None:
        if self.extra is None:
            self.extra = {}


class BaseAdapter(ABC):
    """사이트별 파서는 이 클래스를 상속해 fetch_listings/parse_detail만 구현하면 된다."""

    source: str = "base"

    @abstractmethod
    def fetch_listing_urls(self, params: SearchParams) -> Iterator[str]:
        """검색 결과 목록에서 매물 상세 URL들을 yield한다."""

    @abstractmethod
    def parse_detail(self, url: str) -> Listing:
        """매물 상세 URL을 정규화된 Listing으로 변환한다."""

    def fetch_normalized_listings(self, params: SearchParams) -> Iterable[Listing]:
        for url in self.fetch_listing_urls(params):
            yield self.parse_detail(url)
