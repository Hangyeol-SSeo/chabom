"""차봄 전용 Encar 로그인 창과 로컬 브라우저 세션을 관리한다.

비밀번호는 차봄에 입력하지 않는다. 사용자가 Encar 창에서 직접 로그인한 뒤
로그인 여부를 확인하고 Playwright의 브라우저 상태만 로컬 파일에 저장한다.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from crawler.browser_fetch import _DEFAULT_USER_AGENT


class EncarSessionManager:
    def __init__(self, state_path: Path):
        self.state_path = state_path
        self._lock = asyncio.Lock()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def status(self) -> str:
        if self._page and not self._page.is_closed():
            return "waiting"
        return "saved" if self.state_path.exists() else "none"

    async def start(self) -> str:
        async with self._lock:
            if self.status() == "waiting":
                return "waiting"
            await self._close_browser()
            try:
                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=False)
                self._context = await self._browser.new_context(
                    locale="ko-KR", user_agent=_DEFAULT_USER_AGENT,
                )
                self._page = await self._context.new_page()
                await self._page.goto("https://fem.encar.com/login", wait_until="domcontentloaded", timeout=20000)
            except Exception:
                await self._close_browser()
                raise
            return "waiting"

    async def complete(self, history_url: str) -> str:
        async with self._lock:
            if self.status() != "waiting":
                raise RuntimeError("먼저 엔카 로그인 창을 열어주세요.")
            probe = None
            try:
                # 로그인 중인 창을 이동시키지 않고 같은 세션의 새 탭에서 상태를 확인한다.
                probe = await self._context.new_page()
                await probe.goto(history_url, wait_until="domcontentloaded", timeout=20000)
                await probe.wait_for_function(
                    "() => location.pathname.includes('/login') || !!document.body?.innerText?.includes('항목순')",
                    timeout=15000,
                )
                if "/login" in probe.url:
                    raise RuntimeError("로그인 화면으로 이동했습니다")
            except Exception as exc:
                if self._page.is_closed():
                    await self._close_browser()
                raise RuntimeError("엔카 로그인을 확인하지 못했습니다. 열린 창에서 로그인한 뒤 다시 확인해주세요.") from exc
            finally:
                if probe:
                    await probe.close()

            state = await self._context.storage_state()
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                descriptor, temporary = tempfile.mkstemp(prefix=".encar_auth_", dir=self.state_path.parent)
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    json.dump(state, output, ensure_ascii=False)
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.state_path)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
            await self._close_browser()
            return "saved"

    async def cancel(self) -> str:
        async with self._lock:
            await self._close_browser()
            return self.status()

    async def close(self) -> None:
        async with self._lock:
            await self._close_browser()

    async def _close_browser(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._playwright:
                await self._playwright.stop()
            self._page = self._context = self._browser = self._playwright = None
