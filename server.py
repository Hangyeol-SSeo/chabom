"""매물 조회·검증 API 서버 (FastAPI) — 2026-10-01 웹 배포용으로 개편.

**배경**: 2026-09-18 개편으로 이 프로그램은 "많이 찾아주는 도구"에서 "한 건을 깊게 검증하는
도구"로 바뀌었다. 사용자가 각 사이트의 자체 검색으로 후보를 찾고, 매물 링크 하나를 주면(또는
직접 입력하면) 그 한 건만 열어서 검증한다. 판정은 0~100 점수가 아니라 항목별 PASS/FAIL/미확인
체크리스트이며, 성능기록부·보험이력처럼 크롤링으로 못 가져오는 항목은 사용자가 직접 확인해
입력하지 않으면 "미확인"으로 게이팅되어 전체 판정이 "보류"로 강제된다(scoring/checklist.py).

**웹 배포(2026-10-01)**: 여러 사용자가 인터넷에서 쓰도록 바뀌었다.
- 화면(web/)은 Firebase Hosting이, 로그인은 Firebase Auth(Google·카카오)가 맡는다.
- 차량 보관함·딜러 기록은 브라우저가 사용자별 Firestore 문서(`users/{uid}/...`)에 직접 저장한다
  (web/store.js, firestore.rules). 그래서 이 서버는 사용자 데이터를 저장하지 않는다.
- 이 서버는 Cloud Run(서울)에서 돌며, 브라우저 없이는 할 수 없는 두 가지만 맡는다.
  `/api/lookup`은 Playwright로 매물 페이지 한 건을 열고, `/api/verify`는 체크리스트를 판정한다.
- 모든 요청은 Firebase ID 토큰과 허용 이메일 목록으로 검사한다(api/auth.py). 링크 조회는
  사용자별 일일 한도(LOOKUP_DAILY_LIMIT)를 두고, 한 번에 한 건씩 순서대로 처리한다.

로컬 개발(Firebase 에뮬레이터 사용)은 README의 "로컬 개발" 절과 docs/deploy.md를 참고한다.
"""
from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.auth import FirebaseGateway, User, authorize, bearer_token
from api.validation import normalize_phone, normalize_url
from crawler.adapters.bobaedream_adapter import BobaedreamAdapter
from crawler.adapters.encar_detail_adapter import EncarDetailAdapter
from crawler.adapters.kbchachacha_adapter import KbchachachaAdapter
from crawler.adapters.kcar_detail_adapter import KcarDetailAdapter
from crawler.browser_fetch import BrowserFetcher
from crawler.rate_limiter import RateLimiter
from normalizer.schema import Listing
from scoring.checklist import ChecklistResult, DealerStatus, evaluate_checklist
from scoring.scorer import load_weights

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEV_MODE = os.environ.get('CHABOM_DEV') == '1'
LOOKUP_DAILY_LIMIT = int(os.environ.get('LOOKUP_DAILY_LIMIT', '30'))
# Firebase Hosting의 Cloud Run rewrite는 60초에 끊긴다. 그 전에 사용자에게 안내 문구로 답한다.
LOOKUP_TIMEOUT_SEC = float(os.environ.get('LOOKUP_TIMEOUT_SEC', '50'))
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get('ALLOWED_ORIGINS', '').split(',') if o.strip()]

# Playwright 동기 API 객체는 만든 스레드에서만 써야 한다. 브라우저 작업은 전부 이 단일 스레드에서
# 순서대로 처리한다 — 여러 사용자가 동시에 조회해도 사이트에는 한 번에 한 요청만 간다.
_browser_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='browser')
# 컨테이너의 /dev/shm은 작아서 Chromium이 그대로 쓰면 큰 페이지에서 죽는다.
_fetcher = BrowserFetcher(block_heavy_resources=True, launch_args=['--disable-dev-shm-usage'])
_weights = load_weights()
gateway = FirebaseGateway()

# URL의 호스트로 소스를 식별한다. 링크 1건만 여는 이 엔드포인트는 "사람이 그 링크를 클릭하는
# 것"과 실질적으로 같은 단발 조회라는 게 이 프로젝트의 판단이다(2026-09-20, 사용자 피드백으로
# 정정). 보배드림·KB차차차는 애초에 상세페이지 robots.txt 제한이 없어 이 판단이 필요 없었다.
# 케이카·엔카는 상세페이지가 지금도 robots.txt에 Disallow로 명시되어 있지만, "자동화된 반복
# 수집을 막는 규약"과 "사용자가 준 링크 1건을 여는 단발 조회"를 다른 범주로 재해석해 의식적
# 예외로 지원한다 — 각 어댑터 모듈 docstring 참고(kcar_detail_adapter.py, encar_detail_adapter.py).
# 웹 공개(2026-10-01) 후에도 사용자 결정으로 네 곳을 모두 유지한다. 대신 허용된 계정만,
# 사용자별 일일 한도 안에서, 사이트별 간격을 두고 한 건씩 연다 — crawler/compliance.py 참고.
_SOURCE_HOSTS = {
    "bobaedream.co.kr": ("bobaedream", True),
    "kbchachacha.com": ("kbchachacha", True),
    "kcar.com": ("kcar", True),
    "encar.com": ("encar", True),
}

_ADAPTERS = {
    "bobaedream": lambda: BobaedreamAdapter(fetcher=_fetcher),
    "kbchachacha": lambda: KbchachachaAdapter(fetcher=_fetcher),
    "kcar": lambda: KcarDetailAdapter(fetcher=_fetcher),
    "encar": lambda: EncarDetailAdapter(fetcher=_fetcher),
}
# 사용자가 여럿이어도 같은 사이트를 연달아 두드리지 않도록 사이트별로 간격을 둔다.
_SOURCE_LIMITERS = {source: RateLimiter(1.0, 3.0) for source in _ADAPTERS}


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    yield
    # 브라우저는 만든 스레드에서 닫는다. 실행기는 남겨 두어 다음 조회 때 브라우저를 다시 띄운다.
    _browser_executor.submit(_fetcher.close).result(timeout=30)


app = FastAPI(title="차봄 — 내 차를 고르는 공간", lifespan=_lifespan)
if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["POST"],
        allow_headers=["Authorization", "Content-Type"],
    )


def current_user(token: str = Depends(bearer_token)) -> User:
    return authorize(gateway, token)


def _identify_source(url: str) -> tuple[Optional[str], bool]:
    host = urlparse(url).netloc.lower()
    for suffix, (source, supported) in _SOURCE_HOSTS.items():
        if host == suffix or host.endswith('.' + suffix):
            return source, supported
    return None, False


class LookupRequest(BaseModel):
    url: str = Field(max_length=2048)


@app.get('/api/health')
def health() -> dict:
    return {'ok': True}


@app.post("/api/lookup")
async def lookup(req: LookupRequest, user: User = Depends(current_user)) -> dict:
    """링크 하나를 열어 정규화된 필드를 돌려준다. 저장은 브라우저가 사용자 Firestore에 한다."""
    try:
        url = normalize_url(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    source, supported = _identify_source(url)
    if source is None or not supported:
        return {'url': url, 'source': source or '', **_unsupported(url, source)}
    if not gateway.consume_lookup(user.uid, LOOKUP_DAILY_LIMIT):
        raise HTTPException(status_code=429,
                            detail=f'오늘 링크 조회 한도({LOOKUP_DAILY_LIMIT}회)를 모두 썼습니다. 직접 입력은 계속 사용할 수 있습니다.')
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(loop.run_in_executor(_browser_executor, _lookup, url, source),
                                        timeout=LOOKUP_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        logger.warning('단건 조회 시간 초과: %s', url)
        result = {'ok': False, 'reason': '사이트 응답이 늦어 조회를 마치지 못했습니다. 잠시 후 다시 시도하거나 직접 입력해주세요.'}
    return {'url': url, 'source': source, **result}


def _unsupported(url: str, source: Optional[str]) -> dict:
    if source is None:
        return {"ok": False, "reason": f"인식할 수 없는 사이트입니다: {url}\n직접 입력 모드를 사용하세요."}
    return {
        "ok": False,
        "reason": (
            f"{source}는 상세페이지가 robots.txt로 막혀 있어 자동으로 열지 않습니다 "
            "(crawler/compliance.py 참고). 페이지를 직접 열어 보이는 정보를 아래 폼에 입력해주세요."
        ),
    }


def _lookup(url: str, source: str) -> dict:
    """브라우저 스레드에서만 호출한다 — 대량 크롤링이 아니라 이 한 건만 연다."""
    _SOURCE_LIMITERS[source].wait()
    adapter = _ADAPTERS[source]()
    try:
        listing = adapter.parse_detail(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("단건 조회 실패: %s (%s)", url, exc)
        return {"ok": False, "reason": f"조회에 실패했습니다: {exc}"}
    return {"ok": True, "listing": listing.to_dict()}


class DealerRecord(BaseModel):
    blacklisted: bool = False
    reason: str = Field(default='', max_length=1000)
    blacklisted_at: str = Field(default='', max_length=64)


class PhoneMatch(BaseModel):
    source: str = Field(max_length=40)
    dealer_key: str = Field(max_length=200)
    display_name: str = Field(default='', max_length=200)
    reason: str = Field(default='', max_length=1000)


class DealerContext(BaseModel):
    """사용자 본인의 딜러 기록 중 이 매물 판정에 필요한 부분 — 브라우저가 Firestore에서 읽어 보낸다."""
    record: Optional[DealerRecord] = None
    phone_matches: list[PhoneMatch] = Field(default_factory=list, max_length=100)


class VerifyRequest(BaseModel):
    listing: dict
    dealer_context: Optional[DealerContext] = None


@app.post("/api/verify")
def verify(req: VerifyRequest, user: User = Depends(current_user)) -> dict:
    """listing 페이로드(정규화 스키마 형태, 부분 입력 가능)를 받아 체크리스트를 평가한다.

    프레임손상/침수/전손/보험이력공개 같은 핵심 필드는 자동 조회로 채워지지 않는 게 정상이다 —
    사용자가 화면에서 직접 확인해 넣지 않으면 evaluate_checklist()가 "미확인"으로 게이팅한다.
    """
    data = dict(req.listing)
    data.setdefault("listing_id", "manual")
    data.setdefault("source", "manual")
    try:
        listing = Listing.from_dict(data)
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail='차량 정보 형식이 올바르지 않습니다.') from exc
    dealer_status = _build_dealer_status(listing, req.dealer_context)
    result = evaluate_checklist(listing, dealer_status=dealer_status, weights=_weights)
    return _result_to_dict(result, listing)


def _build_dealer_status(listing: Listing, context: Optional[DealerContext]) -> DealerStatus:
    source, dealer_key = listing.source, listing.dealer.dealer_id
    if not dealer_key:
        return DealerStatus(known=False)
    context = context or DealerContext()
    record = context.record or DealerRecord()
    phone_matches = []
    if normalize_phone(listing.dealer.phone):
        phone_matches = [m.model_dump() for m in context.phone_matches
                         if (m.source, m.dealer_key) != (source, dealer_key)]
    return DealerStatus(
        source=source, dealer_key=dealer_key, known=True,
        blacklisted=record.blacklisted, reason=record.reason if record.blacklisted else "",
        blacklisted_at=record.blacklisted_at if record.blacklisted else "", phone_matches=phone_matches,
    )


def _result_to_dict(result: ChecklistResult, listing: Listing) -> dict:
    return {
        "overall": result.overall,
        "hold_reasons": result.hold_reasons,
        "items": [
            {"key": i.key, "label": i.label, "verdict": i.verdict, "detail": i.detail, "critical": i.critical}
            for i in result.items
        ],
        "dealer_status": (
            {
                "source": result.dealer_status.source,
                "dealer_key": result.dealer_status.dealer_key,
                "known": result.dealer_status.known,
                "blacklisted": result.dealer_status.blacklisted,
                "reason": result.dealer_status.reason,
                "phone_matches": result.dealer_status.phone_matches,
            }
            if result.dealer_status else None
        ),
        "listing": listing.to_dict(),
    }


if DEV_MODE:
    # 로컬 개발 전용: Hosting이 배포 시 자동으로 제공하는 /__/firebase/init.json을 흉내 내고
    # 화면도 같은 출처에서 내준다. demo- 프로젝트 ID를 보면 web/firebase.js가 에뮬레이터에 붙는다.
    @app.get('/__/firebase/init.json')
    def firebase_init() -> dict:
        project = os.environ.get('GOOGLE_CLOUD_PROJECT', 'demo-chabom')
        return {'projectId': project, 'apiKey': 'demo-api-key', 'authDomain': f'{project}.firebaseapp.com'}

    app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "web"), html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '8000')))
