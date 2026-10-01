"""사용자가 사이트를 탐색하는 브라우저의 세션을 단건 조회에 연결한다."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from crawler.browser_fetch import _DEFAULT_USER_AGENT


class SiteSessionManager:
    def __init__(self, state_path: Path, home_url: str):
        self.state_path = state_path
        self.home_url = home_url
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
            if self._context:
                try:
                    await self._save_state()
                except Exception:
                    pass  # 사용자가 창을 직접 닫았으면 이전 저장 상태로 다시 시작한다.
            await self._close_browser()
            try:
                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=False)
                options = {"locale": "ko-KR", "user_agent": _DEFAULT_USER_AGENT}
                if self.state_path.exists():
                    options["storage_state"] = str(self.state_path)
                self._context = await self._browser.new_context(**options)
                self._page = await self._context.new_page()
                await self._page.goto(self.home_url, wait_until="domcontentloaded", timeout=20000)
            except Exception:
                await self._close_browser()
                raise
            return "waiting"

    async def snapshot(self) -> bool:
        """열린 탐색 창의 현재 쿠키/사이트 상태를 다음 조회 컨텍스트에 복사한다."""
        async with self._lock:
            if not self._context:
                return False
            await self._save_state()
            return True

    async def _save_state(self) -> None:
        state = await self._context.storage_state()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".site_auth_", dir=self.state_path.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(state, output, ensure_ascii=False)
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.state_path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    async def cancel(self) -> str:
        async with self._lock:
            try:
                if self._context:
                    await self._save_state()
            finally:
                await self._close_browser()
            return self.status()

    async def close(self) -> None:
        async with self._lock:
            try:
                if self._context:
                    await self._save_state()
            finally:
                await self._close_browser()

    async def _close_browser(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._playwright:
                await self._playwright.stop()
            self._page = self._context = self._browser = self._playwright = None
