"""기존 Encar 로그인 확인 API를 제공하는 사이트 세션 관리자."""
from __future__ import annotations

from pathlib import Path

from crawler.site_session import SiteSessionManager


class EncarSessionManager(SiteSessionManager):
    def __init__(self, state_path: Path):
        super().__init__(state_path, "https://fem.encar.com/")

    async def complete(self, history_url: str) -> str:
        """이전 버전의 명시적 로그인 확인 API와 호환한다."""
        async with self._lock:
            if self.status() != "waiting":
                raise RuntimeError("먼저 엔카 로그인 창을 열어주세요.")
            probe = None
            try:
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

            await self._save_state()
            await self._close_browser()
            return "saved"
