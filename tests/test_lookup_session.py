"""단건 조회의 브라우저 세션 수명 회귀 테스트. 실제 사이트나 브라우저를 열지 않는다.

Playwright sync 세션은 만든 스레드에서만 쓰고 닫을 수 있고 한 스레드에 하나만 열 수 있다.
서버가 세션 하나를 공유하던 시절에는 종료 시 "Cannot switch to a different thread"가 났고,
다른 사이트를 조회한 뒤의 엔카 조회가 실패했다 — 조회마다 열고 같은 스레드에서 닫아야 한다.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import server
from normalizer.schema import Listing


class FakeFetcher:
    def __init__(self, log: list):
        self.log = log
        self.owner = threading.get_ident()
        self.closed_in = None

    def close(self) -> None:
        self.closed_in = threading.get_ident()
        self.log.append(self)


def fake_adapter(log: list, fail: bool = False):
    class Adapter:
        def __init__(self):
            self.fetcher = FakeFetcher(log)

        def parse_detail(self, url: str) -> Listing:
            if fail:
                raise RuntimeError("판매 종료")
            return Listing(listing_id="1", source="bobaedream", url=url)

    return Adapter


def use_fake(monkeypatch, log: list, fail: bool = False) -> None:
    monkeypatch.setattr(server, "_SOURCE_HOSTS", {"bobaedream.example.com": ("bobaedream", True)})
    monkeypatch.setitem(server._ADAPTERS, "bobaedream", fake_adapter(log, fail))


def test_server_keeps_no_shared_browser_session():
    assert not hasattr(server, "_fetcher")
    for source, factory in server._ADAPTERS.items():
        assert factory().fetcher is not factory().fetcher, source


def test_lookup_closes_its_own_session_in_the_same_thread(monkeypatch):
    log: list = []
    use_fake(monkeypatch, log)
    urls = [f"https://bobaedream.example.com/car?no={i}" for i in range(6)]
    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(lambda url: server._lookup(server.LookupRequest(url=url)), urls))
    assert all(result["ok"] for result in results)
    assert len(log) == len(set(map(id, log))) == 6  # 조회마다 세션 하나, 공유 없음
    assert all(fetcher.closed_in == fetcher.owner for fetcher in log)


def test_lookup_closes_session_when_the_page_fails(monkeypatch):
    log: list = []
    use_fake(monkeypatch, log, fail=True)
    result = server._lookup(server.LookupRequest(url="https://bobaedream.example.com/car?no=1"))
    assert result["ok"] is False and "판매 종료" in result["reason"]
    assert len(log) == 1 and log[0].closed_in == log[0].owner


def test_failed_session_cleanup_does_not_hide_the_result(monkeypatch):
    log: list = []
    use_fake(monkeypatch, log)

    def broken_close(self) -> None:
        raise RuntimeError("브라우저가 이미 종료됨")

    monkeypatch.setattr(FakeFetcher, "close", broken_close)
    result = server._lookup(server.LookupRequest(url="https://bobaedream.example.com/car?no=1"))
    assert result["ok"] is True
