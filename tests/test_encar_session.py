"""Encar 로그인 세션 저장은 합성 브라우저 상태만 사용해 검증한다."""
from __future__ import annotations

import asyncio
import json
import stat

import pytest

from crawler.encar_session import EncarSessionManager


class FakePage:
    def __init__(self, logged_in: bool):
        self.logged_in = logged_in
        self.visited = []
        self.closed = False

    def is_closed(self):
        return self.closed

    async def goto(self, url, **_kwargs):
        self.visited.append(url)
        self.url = url if self.logged_in else "https://login.example.com/login"

    async def wait_for_function(self, _script, **_kwargs):
        return None

    async def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, logged_in):
        self.probe = FakePage(logged_in)

    async def new_page(self):
        return self.probe

    async def storage_state(self):
        return {"cookies": [{"name": "synthetic-session", "value": "test-only"}], "origins": []}


class FakeBrowser:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakePlaywright:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


def _manager(tmp_path, logged_in):
    manager = EncarSessionManager(tmp_path / "encar_auth_state.json")
    manager._page = FakePage(logged_in)
    manager._context = FakeContext(logged_in)
    manager._browser = FakeBrowser()
    manager._playwright = FakePlaywright()
    return manager


def test_completed_login_saves_private_reusable_state(tmp_path):
    manager = _manager(tmp_path, logged_in=True)
    assert manager.status() == "waiting"
    assert asyncio.run(manager.complete("https://history.example.com/history?carId=123")) == "saved"
    assert manager.status() == "saved"
    assert json.loads(manager.state_path.read_text())["cookies"][0]["name"] == "synthetic-session"
    assert stat.S_IMODE(manager.state_path.stat().st_mode) == 0o600


def test_unverified_login_never_overwrites_saved_state(tmp_path):
    manager = _manager(tmp_path, logged_in=False)
    manager.state_path.write_text('{"existing":"session"}')
    with pytest.raises(RuntimeError, match="로그인을 확인하지 못했습니다"):
        asyncio.run(manager.complete("https://history.example.com/history?carId=123"))
    assert manager.state_path.read_text() == '{"existing":"session"}'
    assert manager.status() == "waiting"
