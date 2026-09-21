"""storage/dealers.py(딜러 블랙리스트 sqlite) 단위 테스트 — 임시 파일 DB 사용."""
from __future__ import annotations

from storage import dealers as store


def make_conn(tmp_path):
    return store.get_connection(tmp_path / "dealers.db")


def test_normalize_phone_strips_non_digits():
    assert store.normalize_phone("010-0000-0000") == "01000000000"
    assert store.normalize_phone(None) == ""


def test_get_dealer_returns_none_when_absent(tmp_path):
    conn = make_conn(tmp_path)
    assert store.get_dealer(conn, "bobaedream", "does-not-exist") is None


def test_upsert_then_get_dealer(tmp_path):
    conn = make_conn(tmp_path)
    store.upsert_dealer(conn, "bobaedream", "abc123", display_name="테스트판매자", phone="010-0000-0001", region="서울")
    row = store.get_dealer(conn, "bobaedream", "abc123")
    assert row is not None
    assert row["blacklisted"] is False
    assert row["phone"] == "01000000001"


def test_blacklist_dealer_sets_flag_and_reason(tmp_path):
    conn = make_conn(tmp_path)
    store.blacklist_dealer(conn, "bobaedream", "abc123", reason="침수 이력 은폐", phone="010-0000-0001")
    row = store.get_dealer(conn, "bobaedream", "abc123")
    assert row["blacklisted"] is True
    assert row["reason"] == "침수 이력 은폐"
    assert row["blacklisted_at"]


def test_blacklist_requires_dealer_key(tmp_path):
    conn = make_conn(tmp_path)
    try:
        store.blacklist_dealer(conn, "bobaedream", "", reason="사유")
        assert False, "should have raised"
    except ValueError:
        pass


def test_unblacklist_clears_flag(tmp_path):
    conn = make_conn(tmp_path)
    store.blacklist_dealer(conn, "bobaedream", "abc123", reason="사유")
    store.unblacklist_dealer(conn, "bobaedream", "abc123")
    row = store.get_dealer(conn, "bobaedream", "abc123")
    assert row["blacklisted"] is False
    assert row["reason"] is None


def test_find_by_phone_matches_across_sources(tmp_path):
    conn = make_conn(tmp_path)
    store.blacklist_dealer(conn, "bobaedream", "abc123", reason="사유A", phone="010-0000-0001")
    matches = store.find_by_phone(conn, "01000000001")
    assert len(matches) == 1
    assert matches[0]["source"] == "bobaedream"


def test_find_by_phone_excludes_self(tmp_path):
    conn = make_conn(tmp_path)
    store.blacklist_dealer(conn, "bobaedream", "abc123", reason="사유A", phone="010-0000-0001")
    matches = store.find_by_phone(conn, "01000000001", exclude=("bobaedream", "abc123"))
    assert matches == []


def test_find_by_phone_ignores_non_blacklisted(tmp_path):
    conn = make_conn(tmp_path)
    store.upsert_dealer(conn, "bobaedream", "abc123", phone="010-0000-0001")
    matches = store.find_by_phone(conn, "01000000001")
    assert matches == []


def test_list_blacklisted_returns_only_blacklisted(tmp_path):
    conn = make_conn(tmp_path)
    store.upsert_dealer(conn, "bobaedream", "clean-dealer", phone="010-0000-0000")
    store.blacklist_dealer(conn, "kbchachacha", "bad-dealer", reason="전손 은폐")
    rows = store.list_blacklisted(conn)
    assert len(rows) == 1
    assert rows[0]["dealer_key"] == "bad-dealer"


def test_favorite_persists_and_blacklist_removes_favorite(tmp_path):
    path = tmp_path / 'dealers.db'
    conn = store.get_connection(path)
    store.set_favorite(conn, 'encar', '123', True, display_name='테스트판매자', phone='010-0000-0001')
    conn.close()
    conn = store.get_connection(path)
    assert store.get_dealer(conn, 'encar', '123')['favorite']
    store.blacklist_dealer(conn, 'encar', '123', '기록 불일치')
    row = store.get_dealer(conn, 'encar', '123')
    assert row['blacklisted'] and not row['favorite']
    import pytest
    with pytest.raises(ValueError, match='블랙리스트'):
        store.set_favorite(conn, 'encar', '123', True)
    store.unblacklist_dealer(conn, 'encar', '123')
    store.set_favorite(conn, 'encar', '123', True)
    assert store.list_dealers(conn)[0]['favorite']
    store.set_favorite(conn, 'encar', '123', False)
    assert not store.get_dealer(conn, 'encar', '123')['favorite']
    conn.close()


def test_migrate_old_database_preserves_blacklist(tmp_path):
    import sqlite3
    path = tmp_path / 'old.db'
    conn = sqlite3.connect(path)
    conn.executescript(store.SCHEMA.replace('    favorite INTEGER NOT NULL DEFAULT 0,\n', ''))
    conn.execute("INSERT INTO dealers (source,dealer_key,blacklisted,reason,created_at,updated_at) VALUES ('encar','123',1,'기존 사유','2026-01-01','2026-01-01')")
    conn.commit()
    conn.close()
    for _ in range(2):
        conn = store.get_connection(path)
        row = store.get_dealer(conn, 'encar', '123')
        assert row['blacklisted'] and row['reason'] == '기존 사유'
        assert not row['favorite']
        conn.close()


def test_favorite_identity_is_scoped_by_site(tmp_path):
    conn = make_conn(tmp_path)
    store.set_favorite(conn, 'encar', '123', True, display_name='같은 이름')
    store.set_favorite(conn, 'kcar', '123', False, display_name='같은 이름')
    rows = store.list_dealers(conn)
    assert len(rows) == 2
    assert store.get_dealer(conn, 'encar', '123')['favorite']
    assert not store.get_dealer(conn, 'kcar', '123')['favorite']
    conn.close()


def test_dealer_api_rejects_missing_identity_and_blacklist_conflict(tmp_path, monkeypatch):
    import pytest
    import server
    from fastapi import HTTPException
    monkeypatch.setattr(server, 'DEALERS_DB_PATH', tmp_path / 'dealers.db')
    req = server.DealerFavoriteRequest(source='encar', dealer_key='123', favorite=True)
    assert server.favorite_dealer(req)['ok']
    assert server.get_dealers()['dealers'][0]['favorite']
    server.blacklist_dealer(server.BlacklistRequest(source='encar', dealer_key='123', reason='문제 발견'))
    assert not server.get_dealers()['dealers'][0]['favorite']
    with pytest.raises(HTTPException) as error:
        server.favorite_dealer(req)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        server.favorite_dealer(server.DealerFavoriteRequest(source='encar', dealer_key=' ', favorite=True))
    assert error.value.status_code == 422
    with pytest.raises(HTTPException):
        server.blacklist_dealer(server.BlacklistRequest(source='encar', dealer_key='123', reason=' '))
