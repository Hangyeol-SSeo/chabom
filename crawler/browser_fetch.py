"""Playwright(실브라우저) 기반 요청 계층.

**배경(2026-09-17)**: 이전 버전은 `requests`로 미리 전량을 크롤링해 정적 데이터셋을 만들어
두는 방식이었다. 사용자가 이를 "실제 사이트에서 검색하는 느낌"으로 바꿔달라고 요청했고,
필터를 설정하고 검색 결과를 가져오는 동작은 실브라우저(Selenium/Playwright) 기반이 더
적합하다고 판단했다 — 이 프로젝트는 개인 용도 도구이므로 그 방향을 그대로 따른다.

한 번 띄운 브라우저 컨텍스트를 여러 어댑터가 공유해서 재사용한다(검색 1회당 브라우저를
새로 띄우면 느리다). robots.txt를 준수하는 소스만 이 계층으로 요청하며(crawler/compliance.py),
사이트가 실제 봇 차단(캡차/챌린지 페이지)을 걸면 우회하지 않고 그대로 실패로 처리한다 —
User-Agent 위장이나 자동화 감지 회피 스크립트는 넣지 않는다.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class BrowserFetcher:
    """헤드리스 Chromium 세션 하나를 여러 어댑터/요청이 공유하는 래퍼."""

    def __init__(self, headless: bool = True):
        self._headless = headless
        self._pw = None
        self._browser = None
        self._context = None

    def _ensure_started(self) -> None:
        if self._context is not None:
            return
        # 모듈 최상단이 아니라 여기서 import하는 이유: playwright가 설치되어 있지 않아도
        # (예: score 전용 CLI 사용, 유닛테스트) 이 프로젝트의 나머지 기능은 그대로 동작해야 한다.
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless)
        self._context = self._browser.new_context(
            user_agent=_DEFAULT_USER_AGENT,
            locale="ko-KR",
        )

    def get_html(
        self,
        url: str,
        timeout_sec: float = 20.0,
        wait_selector: Optional[str] = None,
        warmup_url: Optional[str] = None,
        post_wait_ms: int = 0,
    ) -> Optional[str]:
        """페이지를 열어 렌더링된 HTML 전체를 반환한다. 실패 시 None.

        `warmup_url`: 케이카처럼 검색 페이지를 거치지 않고 상세페이지 URL로 바로 들어가면
        실제 매물 데이터(가격·진단결과 등)가 "0" 같은 빈 템플릿으로만 채워지는 사이트가 있다
        (실측 확인 — robots.txt/차단이 아니라 그 사이트 프론트엔드가 세션/쿠키를 기대하는
        구조라서다). 이 경우 같은 페이지 객체로 `warmup_url`을 먼저 방문해 쿠키를 확보한 뒤
        본 URL로 이동한다 — 사람이 검색 결과에서 매물을 클릭해 들어가는 것과 같은 순서다.
        `post_wait_ms`: `wait_selector`는 엘리먼트가 DOM에 존재하는지만 보고 그 안의 텍스트가
        최종 값으로 채워졌는지는 보장하지 않는다(Vue 반응형 데이터가 늦게 채워지는 위젯이
        있음) — 그런 경우를 위한 추가 고정 대기.
        """
        self._ensure_started()
        page = self._context.new_page()
        try:
            if warmup_url:
                try:
                    page.goto(warmup_url, timeout=timeout_sec * 1000, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                except Exception as exc:
                    logger.info("워밍업 요청 실패(무시하고 진행): %s (%s)", warmup_url, exc)
            page.goto(url, timeout=timeout_sec * 1000, wait_until="domcontentloaded")
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout_sec * 1000)
                except Exception:
                    pass  # 셀렉터가 없어도(빈 결과 등) 지금까지의 HTML은 그대로 반환
            if post_wait_ms:
                page.wait_for_timeout(post_wait_ms)
            return page.content()
        except Exception as exc:
            logger.warning("페이지 로드 실패: %s (%s)", url, exc)
            return None
        finally:
            page.close()

    def new_page(self):
        """공유 브라우저 컨텍스트에서 새 탭(Page)을 직접 내준다.

        `get_html()`은 HTML 문자열만 반환해 페이지에 주입된 JS 상태(예: 엔카의
        `window.__PRELOADED_STATE__`)를 읽을 수 없다 — 그런 어댑터(encar_detail_adapter.py)는
        이 메서드로 Page 객체를 직접 받아 `page.evaluate()`를 쓴다. 호출부가 반드시
        `page.close()`로 닫아야 한다.
        """
        self._ensure_started()
        return self._context.new_page()

    def get_json(
        self,
        url: str,
        params: Optional[dict] = None,
        timeout_sec: float = 20.0,
    ) -> Optional[dict]:
        """브라우저 컨텍스트의 요청 API로 JSON을 가져온다(별도 탭 없이 가볍게 처리)."""
        self._ensure_started()
        try:
            resp = self._context.request.get(url, params=params, timeout=timeout_sec * 1000)
            if not resp.ok:
                logger.warning("API 요청 실패: %s (status=%s)", url, resp.status)
                return None
            return resp.json()
        except Exception as exc:
            logger.warning("API 요청 실패: %s (%s)", url, exc)
            return None

    def close(self) -> None:
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
        self._context = self._browser = self._pw = None

    def __enter__(self) -> "BrowserFetcher":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
