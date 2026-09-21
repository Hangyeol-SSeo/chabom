"""사이트별 robots.txt 확인 결과 기록 (스펙 3.1, 8.1 — 코드 작성 전 필수 확인 사항).

확인 방법: `curl -A "Mozilla/5.0 (compatible; research-bot/1.0)" https://<host>/robots.txt`
확인 시각: 2026-09-16 (kcar/kbchachacha 항목은 2026-09-17 재조사로 갱신). robots.txt는 사이트 운영자가
언제든 바꿀 수 있으므로, 실제 운영 전에는 재확인이 필요하다. 이 파일은 "이용약관"이 아니라
"robots.txt(기술적 신호)"만 다룬다 — 이용약관은 법적 조건이므로 별도로 사람이 직접 검토해야 한다(스펙 3.1, 9장).

**2026-09-17 정정 사항**: 최초 조사에서 kcar/kbchachacha를 "상세페이지 전체 차단"으로 오판했었다.
재조사 결과 kbchachacha는 disallow된 경로(`/public/review/car/detail.kbc` = 리뷰 작성 페이지)와
실제 매물 상세페이지(`/public/car/detail.kbc` = 전혀 다른 경로)를 혼동한 것이었고, 실제 상세페이지는
크롤링 가능하다. kcar는 상세페이지(www.kcar.com)는 여전히 막혀있지만, 실 데이터는 별도 호스트
api.kcar.com(자체 robots.txt 없음)의 목록 API에서 얻을 수 있다는 것을 뒤늦게 발견했다. 상세 내용은
각 항목의 note를 참고.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class ComplianceBlockedError(Exception):
    """robots.txt에 의해 명시적으로 금지된 경로에 접근하려 할 때 발생시킨다."""


@dataclass
class SiteComplianceInfo:
    source: str
    robots_checked_at: str
    generic_ua_allowed: bool  # "User-agent: *" 규칙이 접근을 허용하는가
    disallowed_paths: list[str] = field(default_factory=list)
    detail_page_blocked: bool = False
    note: str = ""
    # robots.txt는 자동화된 크롤러만 규율할 뿐 사람이 브라우저로 직접 보는 것을 막지 않는다.
    # 크롤링이 막힌 사이트라도 사용자가 직접 찾아볼 수 있도록 홈페이지 링크는 안내한다.
    browse_url: str = ""


COMPLIANCE_REGISTRY: dict[str, SiteComplianceInfo] = {
    "bobaedream": SiteComplianceInfo(
        source="bobaedream",
        robots_checked_at="2026-09-16",
        generic_ua_allowed=True,
        disallowed_paths=[],
        detail_page_blocked=False,
        note=(
            "www.bobaedream.co.kr/robots.txt: 'User-agent: *' 에 'Allow: /' 이며 일반 봇에 대한 "
            "Disallow 없음(Amazonbot 등 특정 UA만 차단). 목록/상세 페이지 크롤링 가능. "
            "단, 보험이력 팝업(/mycar/popup/mycarChart_B.php)은 실측 시 15초 내 응답 없음 — "
            "세션/쿠키 요구 또는 카히스토리 실시간 연동으로 추정되며 안정적 자동화가 검증되지 않았음."
        ),
        browse_url="https://www.bobaedream.co.kr/mycar/mycar_list.php",
    ),
    "encar": SiteComplianceInfo(
        source="encar",
        robots_checked_at="2026-09-16",
        generic_ua_allowed=True,
        disallowed_paths=[
            "/dc/dc_cardetailview.do",  # 매물 상세(보험이력/성능기록부 노출 지점)
            "/cars/",
            "/catalog/",
            "/es/record.do",
            "/dc/dc_carsearchlist.do?method=sellerSummary",
        ],
        detail_page_blocked=True,
        note=(
            "www.encar.com/robots.txt: 'Disallow: /dc/dc_cardetailview.do' 로 매물 상세페이지가 "
            "명시적으로 크롤링 금지되어 있음. 게다가 실제 검색/매물 조회 기능은 별도 서브도메인인 "
            "fem.encar.com의 SPA로 옮겨간 상태이며, fem.encar.com/robots.txt는 상세페이지(/cars/detail/, "
            "/cars/report/)뿐 아니라 프론트엔드가 쓰는 것으로 추정되는 내부 데이터 API 경로 "
            "'/v1/readside/'까지 명시적으로 Disallow 처리한다. 그 위에 fem.encar.com 자체가 100% "
            "클라이언트 렌더링(SPA, 빈 HTML 셸)이라 requests+bs4로는 애초에 아무 데이터도 못 얻는다 — "
            "즉 robots.txt(법적/정책적 차단)와 CSR 구조(기술적 한계)가 이중으로 막고 있다. "
            "스펙 3.2 원칙에 따라 공식 API/제휴 데이터(파트너 피드, 유료 API)를 먼저 확인할 것."
        ),
        browse_url="https://www.encar.com",
    ),
    "kcar": SiteComplianceInfo(
        source="kcar",
        robots_checked_at="2026-09-17",
        generic_ua_allowed=True,
        disallowed_paths=[
            "/car/info/",
            "/bc/detail/carInfoDtl?",
            "/br/detail/brandCarInfoDtl?",
        ],
        detail_page_blocked=True,
        note=(
            "www.kcar.com/robots.txt: 매물 상세 관련 경로(/car/info/, /bc/detail/carInfoDtl 등)가 "
            "Disallow 처리되어 있음 — 이 상세페이지는 계속 크롤링하지 않는다. 다만 실제 매물 데이터는 "
            "www.kcar.com이 아니라 별도 호스트 api.kcar.com의 내부 API(예: /bc/stockCar/list)에서 "
            "제공되며, api.kcar.com에는 robots.txt 파일 자체가 없다(404). crawler/adapters/kcar_adapter.py는 "
            "이 목록 API만 사용해 목록 수준 필드(모델명·가격·연식·주행거리·사진 등)를 가져오고, "
            "insurance_history/performance_record는 채우지 않으며 상세페이지는 verification_links로만 "
            "안내한다. 주의: 이 API는 프론트엔드가 자체적으로 쓰는 비공개 내부 API를 개발자도구 네트워크 "
            "탭으로 역추적해 찾은 것이며, 공식 문서화된 공개 API가 아니다 — robots.txt 부재를 명시적 허용의 "
            "근거로 확정할 수는 없고, 사용자가 이 점을 인지한 상태로 진행을 승인했다(README 참고)."
        ),
        browse_url="https://www.kcar.com",
    ),
    "kbchachacha": SiteComplianceInfo(
        source="kbchachacha",
        robots_checked_at="2026-09-17",
        generic_ua_allowed=True,
        disallowed_paths=[
            "/public/review/car/detail.kbc",
            "/public/customer/home/saleCar.kbc",
            "/secured/",
            "/public/login.kbc",
        ],
        detail_page_blocked=False,
        note=(
            "www.kbchachacha.com/robots.txt: Disallow 목록의 '/public/review/car/detail.kbc'는 리뷰 "
            "작성 페이지이고, 실제 매물 상세페이지 경로는 '/public/car/detail.kbc?carSeq=...'로 전혀 "
            "다르며 Disallow 목록에 없다 — 최초 조사에서 두 경로를 혼동해 '상세페이지 전체 차단'으로 "
            "잘못 판단했었다(2026-09-17 재조사로 정정). 실제 상세페이지는 정적 서버 렌더링이며 "
            "사고유무/전손이력/침수이력/용도이력이 그대로 노출되고 제3자 성능점검기관(카모두) 원본 링크도 "
            "포함한다. 목록도 'GET /public/search/list.empty?page=N'이 세션/쿠키 없이 실제 매물 HTML을 "
            "반환해 requests+bs4만으로 충분하다(Playwright 불필요). crawler/adapters/kbchachacha_adapter.py 참고."
        ),
        browse_url="https://www.kbchachacha.com",
    ),
}


def get_compliance_info(source: str) -> SiteComplianceInfo:
    if source not in COMPLIANCE_REGISTRY:
        raise KeyError(f"등록되지 않은 소스입니다: {source}")
    return COMPLIANCE_REGISTRY[source]


def assert_detail_access_allowed(source: str) -> None:
    info = get_compliance_info(source)
    if info.detail_page_blocked:
        browse_hint = (
            f"\n직접 확인: {info.browse_url} (robots.txt는 자동화된 크롤러를 규율할 뿐, "
            "사람이 브라우저로 직접 보는 것까지 막지는 않습니다 — 위 링크에서 직접 검색해 "
            "보험이력/성능기록부를 눈으로 확인하세요.)"
            if info.browse_url else ""
        )
        raise ComplianceBlockedError(
            f"[{source}] robots.txt에서 매물 상세페이지 크롤링이 명시적으로 금지되어 있습니다 "
            f"(Disallow: {', '.join(info.disallowed_paths)}). {info.note}\n"
            "이 프로젝트는 명시적으로 금지된 엔드포인트를 사용하지 않습니다(스펙 3.1). "
            "공식 API/제휴 데이터 제공을 확인하거나, 이용약관까지 재검토한 뒤 진행 여부를 재판단하세요."
            f"{browse_hint}"
        )
