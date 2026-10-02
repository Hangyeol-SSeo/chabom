"""로컬 전용 매물 검증 서버 (FastAPI) — 2026-09-18 전면 개편.

**배경**: 이전 버전(마켓플레이스형 실시간 검색)에 사용자가 다섯 가지 심각한 문제를 제기했다:
(1) robots.txt/봇 차단으로 일괄 크롤링이 원천적으로 불안정, (2) 0~100 합산 점수에 의존하게 되어
핵심 결함이 있어도 "65점"처럼 그럴듯해 보임, (3) 성능기록부·보험이력처럼 가장 중요한 정보를
크롤링으로 확보 못함, (4) 필터가 부족해 원치 않는 차종까지 섞여 나옴, (5) 나쁜 매물을 반복해서
올리는 딜러를 걸러낼 방법이 없음.

그래서 이 프로그램은 **"많이 찾아주는 도구"에서 "한 건을 깊게 검증하는 도구"로 완전히
바뀌었다.** 사용자가 각 사이트의 뛰어난 자체 검색으로 후보를 직접 찾고(문제 4 해결 — 필터를
재구현하지 않는다), 매물 링크 하나를 이 서버에 주면(또는 직접 입력하면) 그 한 건만 열어서
검증한다(문제 1 해결 — 대량 크롤링이 아니라 사람이 링크 하나를 클릭하는 것과 동일한 단발성
조회라 robots.txt/봇 차단 문제에서 자유롭다). 판정은 0~100 점수가 아니라 항목별
PASS/FAIL/미확인 체크리스트이며, 성능기록부·보험이력처럼 크롤링으로 못 가져오는 항목은
**사용자가 직접 확인해서 입력하지 않으면 자동으로 "미확인"으로 게이팅되어 전체 판정이
"보류"로 강제된다**(문제 2, 3 해결 — scoring/checklist.py 참고, "1종 오류(부실차량을 정상으로
오판)는 절대 안 된다"는 사용자 원칙을 판정 로직 자체에 박아 넣었다). 딜러는 sqlite에 누적
저장되어, 한 번 "제외" 등록하면 이후 같은 딜러의 다른 매물을 검증할 때 자동으로 걸린다
(문제 5 해결 — storage/dealers.py 참고, 이름이 아니라 사이트별 판매자 ID로 식별한다).

실행:
    pip install -r requirements.txt
    playwright install chromium   # 최초 1회
    python server.py
    # 브라우저에서 http://localhost:8000 접속
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from contextlib import closing
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from crawler.adapters.bobaedream_adapter import BobaedreamAdapter
from crawler.adapters.encar_detail_adapter import AUTH_STATE_PATH, EncarDetailAdapter
from crawler.encar_session import EncarSessionManager
from crawler.adapters.kbchachacha_adapter import KbchachachaAdapter
from crawler.adapters.kcar_detail_adapter import AUTH_STATE_PATH as KCAR_AUTH_STATE_PATH, KcarDetailAdapter
from crawler.site_session import SiteSessionManager
from normalizer.schema import Listing
from scoring.checklist import ChecklistResult, DealerStatus, evaluate_checklist
from scoring.scorer import load_weights
from storage import dealers as dealer_store
from storage import history as history_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="차봄 — 내 차를 고르는 공간")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_encar_session = EncarSessionManager(AUTH_STATE_PATH)
_kcar_session = SiteSessionManager(KCAR_AUTH_STATE_PATH, "https://www.kcar.com/")
_weights = load_weights()
DEALERS_DB_PATH = Path(__file__).parent / "data/dealers.db"
HISTORY_DB_PATH = Path(__file__).parent / "data/listings.db"

# URL의 호스트로 소스를 식별한다. 링크 1건만 여는 이 엔드포인트는 "사람이 그 링크를 클릭하는
# 것"과 실질적으로 같은 단발 조회라는 게 이 프로젝트의 판단이다(2026-09-20, 사용자 피드백으로
# 정정). 보배드림·KB차차차는 애초에 상세페이지 robots.txt 제한이 없어 이 판단이 필요 없었다.
# 케이카·엔카는 상세페이지가 지금도 robots.txt에 Disallow로 명시되어 있지만, "자동화된 반복
# 수집을 막는 규약"과 "사용자가 준 링크 1건을 여는 단발 조회"를 다른 범주로 재해석해 의식적
# 예외로 지원한다 — 각 어댑터 모듈 docstring 참고(kcar_detail_adapter.py, encar_detail_adapter.py).
_SOURCE_HOSTS = {
    "bobaedream.co.kr": ("bobaedream", True),
    "kbchachacha.com": ("kbchachacha", True),
    "kcar.com": ("kcar", True),
    "encar.com": ("encar", True),
}

# 어댑터는 조회마다 새로 만들고, 각자 자기 브라우저 세션을 연다(_lookup에서 같은 스레드로 닫는다).
# Playwright sync 세션은 만든 스레드에서만 쓰고 닫을 수 있고 한 스레드에 하나만 열 수 있어서,
# 서버 전체가 세션 하나를 공유하면 요청 스레드가 바뀌거나 다른 사이트를 이어서 조회할 때 실패한다.
# Encar·K Car는 저장된 탐색 세션 파일도 이렇게 조회마다 새 컨텍스트에 반영된다.
_ADAPTERS = {
    "bobaedream": BobaedreamAdapter,
    "kbchachacha": KbchachachaAdapter,
    "kcar": KcarDetailAdapter,
    "encar": EncarDetailAdapter,
}


def _identify_source(url: str) -> tuple[Optional[str], bool]:
    host = urlparse(url).netloc.lower()
    for suffix, (source, supported) in _SOURCE_HOSTS.items():
        if host.endswith(suffix):
            return source, supported
    return None, False


class LookupRequest(BaseModel):
    url: str


class EncarSessionCompleteRequest(BaseModel):
    url: str = ""


def _encar_history_probe_url(url: str) -> str:
    candidates = [url] if url else []
    if not candidates:
        with closing(history_store.get_connection(HISTORY_DB_PATH)) as conn:
            candidates = [item.get("url", "") for item in history_store.list_history(conn) if item.get("source") == "encar"]
    for candidate in candidates:
        parsed = urlparse(candidate)
        match = re.fullmatch(r"/cars/detail/(\d+)", parsed.path)
        if parsed.scheme == "https" and parsed.hostname == "fem.encar.com" and match:
            return f"https://car.encar.com/history?carId={match.group(1)}"
    raise HTTPException(status_code=422, detail="로그인을 확인할 엔카 매물 링크가 필요합니다.")


def _require_local_json(request: Request) -> None:
    origin = request.headers.get("origin")
    expected_origin = f"{request.url.scheme}://{request.url.netloc}"
    if request.url.hostname not in {"127.0.0.1", "localhost"} or (origin and origin != expected_origin):
        raise HTTPException(status_code=403, detail="로컬 차봄 화면에서만 로그인 연결을 시작할 수 있습니다.")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="JSON 요청이 필요합니다.")


@app.get("/api/encar/session")
def encar_session_status() -> dict:
    return {"status": _encar_session.status()}


@app.post("/api/encar/session/start")
async def encar_session_start(request: Request) -> dict:
    _require_local_json(request)
    try:
        return {"status": await _encar_session.start()}
    except Exception as exc:
        logger.warning("Encar 로그인 창 열기 실패: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="엔카 로그인 창을 열지 못했습니다. 브라우저 실행 상태를 확인해주세요.") from None


@app.post("/api/encar/session/complete")
async def encar_session_complete(request: Request, req: EncarSessionCompleteRequest) -> dict:
    _require_local_json(request)
    history_url = _encar_history_probe_url(req.url)
    try:
        return {"status": await _encar_session.complete(history_url)}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@app.post("/api/encar/session/cancel")
async def encar_session_cancel(request: Request) -> dict:
    _require_local_json(request)
    return {"status": await _encar_session.cancel()}


@app.get("/api/kcar/session")
def kcar_session_status() -> dict:
    return {"status": _kcar_session.status()}


@app.post("/api/kcar/session/start")
async def kcar_session_start(request: Request) -> dict:
    _require_local_json(request)
    try:
        return {"status": await _kcar_session.start()}
    except Exception as exc:
        logger.warning("K Car 탐색 창 열기 실패: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="케이카 창을 열지 못했습니다. 브라우저 실행 상태를 확인해주세요.") from None


@app.post("/api/kcar/session/cancel")
async def kcar_session_cancel(request: Request) -> dict:
    _require_local_json(request)
    return {"status": await _kcar_session.cancel()}


@app.post("/api/lookup")
async def lookup(req: LookupRequest) -> dict:
    try:
        url = history_store.normalize_url(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    source = _identify_source(url)[0]
    session = {"encar": _encar_session, "kcar": _kcar_session}.get(source)
    if session:
        try:
            await session.snapshot()
        except Exception as exc:
            logger.warning("%s 탐색 창 세션 저장 실패: %s", source, type(exc).__name__)
    result = await run_in_threadpool(_lookup, LookupRequest(url=url))
    if result.get('listing'):
        listing = Listing.from_dict(result['listing'])
        with closing(dealer_store.get_connection(DEALERS_DB_PATH)) as conn:
            dealer_store.upsert_dealer(conn, listing.source, listing.dealer.dealer_id,
                                      display_name=listing.dealer.display_name,
                                      phone=listing.dealer.phone, region=listing.dealer.region)
    with closing(history_store.get_connection(HISTORY_DB_PATH)) as conn:
        result['history_id'] = history_store.record(
            conn, url, source=_identify_source(url)[0] or '', listing=result.get('listing'),
            status='success' if result['ok'] else 'failed', reason=result.get('reason', ''),
        )
    return result


def _stored_check(dealer_conn, listing: Optional[dict]) -> Optional[dict]:
    """저장된 매물을 그대로 판정한다 — 목록·상세에서 결격 사유를 누르지 않고 바로 보여주기 위함."""
    if not listing:
        return None
    try:
        parsed = Listing.from_dict({"listing_id": "manual", "source": "manual", **listing})
        dealer_status = _build_dealer_status(dealer_conn, parsed, remember=False)
        result = evaluate_checklist(parsed, dealer_status=dealer_status, weights=_weights)
    except Exception as exc:  # noqa: BLE001 — 옛 형식의 저장본 하나 때문에 목록 전체가 막히면 안 된다
        logger.info("저장된 매물 판정 실패: %s", type(exc).__name__)
        return None
    check = _result_to_dict(result, parsed)
    del check["listing"], check["dealer_status"]
    return check


@app.get('/api/history')
def get_history() -> dict:
    with closing(history_store.get_connection(HISTORY_DB_PATH)) as conn:
        items = history_store.list_history(conn)
    with closing(dealer_store.get_connection(DEALERS_DB_PATH)) as conn:
        for item in items:
            item['check'] = _stored_check(conn, item['listing'])
    return {'items': items}


class FavoriteRequest(BaseModel):
    favorite: bool


@app.patch('/api/history/{item_id}/favorite')
def favorite_history(item_id: int, req: FavoriteRequest) -> dict:
    with closing(history_store.get_connection(HISTORY_DB_PATH)) as conn:
        if not history_store.set_favorite(conn, item_id, req.favorite):
            raise HTTPException(status_code=404, detail='저장된 매물을 찾을 수 없습니다.')
    return {'ok': True, 'favorite': req.favorite}


def _lookup(req: LookupRequest) -> dict:
    """링크 하나를 열어 정규화된 필드를 미리 채워준다 — 대량 크롤링이 아니라 이 한 건만 연다."""
    source, supported = _identify_source(req.url)
    if source is None:
        return {
            "ok": False,
            "reason": f"인식할 수 없는 사이트입니다: {req.url}\n직접 입력 모드를 사용하세요.",
        }
    if not supported:
        return {
            "ok": False,
            "reason": (
                f"{source}는 상세페이지가 robots.txt로 막혀 있어 자동으로 열지 않습니다 "
                "(crawler/compliance.py 참고). 페이지를 직접 열어 보이는 정보를 아래 폼에 입력해주세요."
            ),
        }
    adapter = _ADAPTERS[source]()
    try:
        listing = adapter.parse_detail(req.url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("단건 조회 실패: %s (%s)", req.url, exc)
        return {"ok": False, "reason": f"조회에 실패했습니다: {exc}"}
    finally:
        try:
            adapter.fetcher.close()
        except Exception as exc:  # noqa: BLE001 — 세션 정리 실패가 조회 결과를 가리면 안 된다
            logger.warning("브라우저 세션 정리 실패: %s", type(exc).__name__)
    return {"ok": True, "listing": listing.to_dict()}


class VerifyRequest(BaseModel):
    listing: dict


@app.post("/api/verify")
def verify(req: VerifyRequest) -> dict:
    """listing 페이로드(정규화 스키마 형태, 부분 입력 가능)를 받아 체크리스트를 평가한다.

    원본 화면에서 확인된 핵심 필드만 자동 반영한다. 확인되지 않은 항목은 사용자가
    직접 확인해 넣지 않으면 evaluate_checklist()가 "미확인"으로 게이팅한다.
    """
    data = dict(req.listing)
    data.setdefault("listing_id", "manual")
    data.setdefault("source", "manual")
    listing = Listing.from_dict(data)

    conn = dealer_store.get_connection(DEALERS_DB_PATH)
    try:
        dealer_status = _build_dealer_status(conn, listing)
    finally:
        conn.close()

    result = evaluate_checklist(listing, dealer_status=dealer_status, weights=_weights)
    if listing.url:
        try:
            url = history_store.normalize_url(listing.url)
        except ValueError:
            pass
        else:
            with closing(history_store.get_connection(HISTORY_DB_PATH)) as conn:
                history_store.record(conn, url, source=listing.source, listing=listing.to_dict())
    return _result_to_dict(result, listing)


class BlacklistRequest(BaseModel):
    source: str
    dealer_key: str
    reason: str
    display_name: str = ""
    phone: str = ""
    region: str = ""


@app.post("/api/dealers/blacklist")
def blacklist_dealer(req: BlacklistRequest) -> dict:
    if not req.dealer_key.strip() or not req.source.strip():
        return {"ok": False, "reason": "이 매물에서 딜러 식별 정보(dealer_key)를 확보하지 못해 등록할 수 없습니다"}
    if not req.reason.strip():
        raise HTTPException(status_code=422, detail='제외 사유를 입력해주세요.')
    conn = dealer_store.get_connection(DEALERS_DB_PATH)
    try:
        dealer_store.blacklist_dealer(
            conn, req.source, req.dealer_key, req.reason,
            display_name=req.display_name, phone=req.phone, region=req.region,
        )
    finally:
        conn.close()
    return {"ok": True}


class UnblacklistRequest(BaseModel):
    source: str
    dealer_key: str


@app.post("/api/dealers/unblacklist")
def unblacklist_dealer(req: UnblacklistRequest) -> dict:
    conn = dealer_store.get_connection(DEALERS_DB_PATH)
    try:
        dealer_store.unblacklist_dealer(conn, req.source, req.dealer_key)
    finally:
        conn.close()
    return {"ok": True}


@app.get("/api/dealers/blacklist")
def list_blacklist() -> dict:
    conn = dealer_store.get_connection(DEALERS_DB_PATH)
    try:
        rows = dealer_store.list_blacklisted(conn)
    finally:
        conn.close()
    return {"dealers": rows}


class DealerFavoriteRequest(BaseModel):
    source: str
    dealer_key: str
    favorite: bool
    display_name: str = ''
    phone: str = ''
    region: str = ''


@app.get('/api/dealers')
def get_dealers() -> dict:
    with closing(dealer_store.get_connection(DEALERS_DB_PATH)) as conn:
        return {'dealers': dealer_store.list_dealers(conn)}


@app.post('/api/dealers/favorite')
def favorite_dealer(req: DealerFavoriteRequest) -> dict:
    if not req.source.strip() or not req.dealer_key.strip():
        raise HTTPException(status_code=422, detail='사이트와 딜러 ID가 필요합니다.')
    with closing(dealer_store.get_connection(DEALERS_DB_PATH)) as conn:
        try:
            dealer_store.set_favorite(conn, **req.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {'ok': True, 'favorite': req.favorite}


def _build_dealer_status(conn, listing: Listing, remember: bool = True) -> DealerStatus:
    """`remember=False`면 딜러를 새로 등록·갱신하지 않고 읽기만 한다(목록 조회용)."""
    source, dealer_key = listing.source, listing.dealer.dealer_id
    if not dealer_key:
        return DealerStatus(known=False)
    phone = dealer_store.normalize_phone(listing.dealer.phone)
    if remember:
        dealer_store.upsert_dealer(conn, source, dealer_key, display_name=listing.dealer.display_name,
                                  phone=listing.dealer.phone, region=listing.dealer.region)
    record = dealer_store.get_dealer(conn, source, dealer_key)
    phone_matches = dealer_store.find_by_phone(conn, phone, exclude=(source, dealer_key)) if phone else []
    if record is None:
        # 처음 보는 딜러면 이번 조회를 계기로 등록해둔다(블랙리스트 아님 — 나중에 조회/등록용 인덱스).
        if remember:
            dealer_store.upsert_dealer(conn, source, dealer_key, phone=listing.dealer.phone, region=listing.dealer.region)
        return DealerStatus(source=source, dealer_key=dealer_key, known=True, phone_matches=phone_matches)
    return DealerStatus(
        source=source, dealer_key=dealer_key, known=True,
        blacklisted=record["blacklisted"], reason=record["reason"] or "",
        blacklisted_at=record["blacklisted_at"] or "", phone_matches=phone_matches,
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


@app.on_event("shutdown")
async def _shutdown() -> None:
    await _encar_session.close()
    await _kcar_session.close()


app.mount("/", StaticFiles(directory="web", html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
