"""robots.txt에서 매물 상세페이지 크롤링이 명시적으로 금지된 사이트용 스텁 어댑터.

엔카(fem.encar.com)는 실측 결과(crawler/compliance.py) 상세페이지뿐 아니라 프론트엔드가 쓰는 것으로
추정되는 내부 데이터 API 경로(/v1/readside/)까지 robots.txt로 명시적으로 금지되어 있고, 그 위에
100% 클라이언트 렌더링(SPA)이라 requests+bs4로는 애초에 데이터를 얻을 수 없다. 스펙 3.1 원칙
("명시적으로 금지된 엔드포인트는 사용하지 않는다")에 따라 여기서는 의도적으로 아무 것도 크롤링하지 않는다.

케이카/KB차차차는 2026-09-17 재조사에서 "전체 차단"이 오판이었음이 드러나
crawler/adapters/kcar_adapter.py, crawler/adapters/kbchachacha_adapter.py로 각각 정식 구현되었다 —
더 이상 이 스텁을 쓰지 않는다.

합법적으로 엔카 데이터를 확보하려면(스펙 3.2, 9장):
  1) 공식 Open API / 파트너 피드 / 유료 데이터 API 제공 여부를 사업자에게 직접 문의
  2) 이용약관을 사람이 직접 검토해 스크래핑 허용 범위를 재확인
  3) 위 경로가 없다면 이 소스는 이 프로젝트 범위에서 제외
"""
from __future__ import annotations

from typing import Iterator

from crawler.base_adapter import BaseAdapter, SearchParams
from crawler.compliance import assert_detail_access_allowed
from normalizer.schema import Listing


class BlockedAdapter(BaseAdapter):
    source = "blocked"

    def fetch_listing_urls(self, params: SearchParams) -> Iterator[str]:
        assert_detail_access_allowed(self.source)
        yield from ()  # pragma: no cover - assert 위에서 항상 예외 발생

    def parse_detail(self, url: str) -> Listing:
        assert_detail_access_allowed(self.source)
        raise AssertionError("unreachable")  # pragma: no cover


class EncarAdapter(BlockedAdapter):
    source = "encar"
