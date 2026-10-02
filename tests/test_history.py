import asyncio
import json
from contextlib import closing

import pytest
from fastapi import HTTPException

from storage import history


def test_repeat_lookup_and_failure_preserve_favorite_and_saved_data(tmp_path):
    path = tmp_path / 'history.db'
    with closing(history.get_connection(path)) as conn:
        item_id = history.record(conn, 'https://example.com/car#photo', listing={'vehicle': {'model': '모닝'}})
        assert history.set_favorite(conn, item_id, True)
        assert history.record(conn, 'https://example.com/car', status='failed', reason='판매 종료') == item_id
    with closing(history.get_connection(path)) as conn:
        items = history.list_history(conn)
        assert len(items) == 1
        assert items[0]['favorite'] is True
        assert items[0]['listing']['vehicle']['model'] == '모닝'
        assert items[0]['status'] == 'failed'
        assert history.set_favorite(conn, item_id, False)
        assert not history.set_favorite(conn, 999, True)


def test_import_existing_snapshots_once(tmp_path):
    from storage.db import get_connection
    path = tmp_path / 'history.db'
    with closing(get_connection(path)) as conn:
        for day, price in [('2026-09-01', 100), ('2026-09-02', 200)]:
            conn.execute('INSERT INTO listing_snapshots (listing_id,source,scraped_at,raw_json) VALUES (?,?,?,?)',
                         ('1', 'test', day, json.dumps({'url': 'https://example.com/1', 'vehicle': {'price_krw': price}})))
        conn.commit()
    with closing(history.get_connection(path)) as conn:
        items = history.list_history(conn)
        assert len(items) == 1
        assert items[0]['origin'] == 'snapshot'
        assert items[0]['listing']['vehicle']['price_krw'] == 200
        history.record(conn, items[0]['url'], listing={'vehicle': {'price_krw': 300}})
    with closing(history.get_connection(path)) as conn:
        assert history.list_history(conn)[0]['listing']['vehicle']['price_krw'] == 300
        assert history.list_history(conn)[0]['origin'] == 'lookup'


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///tmp/a', 'https://', 'https://user:pass@example.com'])
def test_reject_unsafe_links(url):
    with pytest.raises(ValueError):
        history.normalize_url(url)


def test_lookup_and_favorite_endpoints(tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, 'HISTORY_DB_PATH', tmp_path / 'history.db')
    monkeypatch.setattr(server, 'DEALERS_DB_PATH', str(tmp_path / 'dealers.db'))
    monkeypatch.setattr(server, '_lookup', lambda req: {'ok': False, 'reason': '테스트 조회 실패'})
    result = asyncio.run(server.lookup(server.LookupRequest(url='https://example.com/car')))
    item_id = result['history_id']
    assert server.get_history()['items'][0]['status'] == 'failed'
    server.favorite_history(item_id, server.FavoriteRequest(favorite=True))
    assert server.get_history()['items'][0]['favorite'] is True
    with pytest.raises(HTTPException) as error:
        server.favorite_history(999, server.FavoriteRequest(favorite=True))
    assert error.value.status_code == 404


def test_encar_lookup_copies_open_browser_session_first(tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, 'HISTORY_DB_PATH', tmp_path / 'history.db')
    copied = []

    async def snapshot():
        copied.append(True)

    monkeypatch.setattr(server._encar_session, 'snapshot', snapshot)
    monkeypatch.setattr(server, '_identify_source', lambda _url: ('encar', True))

    def fake_lookup(_req):
        assert copied == [True]
        return {'ok': False, 'reason': '합성 조회 실패'}

    monkeypatch.setattr(server, '_lookup', fake_lookup)
    result = asyncio.run(server.lookup(server.LookupRequest(url='https://encar.example.com/cars/detail/00000000')))
    assert result['ok'] is False


def test_kcar_lookup_copies_open_browser_session_first(tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, 'HISTORY_DB_PATH', tmp_path / 'history.db')
    copied = []

    async def snapshot():
        copied.append(True)

    monkeypatch.setattr(server._kcar_session, 'snapshot', snapshot)
    monkeypatch.setattr(server, '_identify_source', lambda _url: ('kcar', True))

    def fake_lookup(_req):
        assert copied == [True]
        return {'ok': False, 'reason': '합성 조회 실패'}

    monkeypatch.setattr(server, '_lookup', fake_lookup)
    result = asyncio.run(server.lookup(server.LookupRequest(url='https://kcar.example.com/bc/detail/carInfoDtl?i_sCarCd=SYNTHETIC')))
    assert result['ok'] is False


def test_verify_saves_manual_link(tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, 'HISTORY_DB_PATH', tmp_path / 'history.db')
    monkeypatch.setattr(server, 'DEALERS_DB_PATH', str(tmp_path / 'dealers.db'))
    result = server.verify(server.VerifyRequest(listing={'url': 'https://example.com/car', 'vehicle': {'model': '모닝'}}))
    assert result['overall'] == 'hold'
    assert server.get_history()['items'][0]['listing']['vehicle']['model'] == '모닝'


def test_history_items_carry_verdict_without_registering_dealers(tmp_path, monkeypatch):
    """목록만 열어도 결격 사유가 바로 보이도록 저장본마다 판정을 붙인다 — 읽기 전용이어야 한다."""
    import server
    from storage import dealers
    monkeypatch.setattr(server, 'HISTORY_DB_PATH', tmp_path / 'history.db')
    monkeypatch.setattr(server, 'DEALERS_DB_PATH', str(tmp_path / 'dealers.db'))
    listing = {
        'source': 'bobaedream', 'listing_id': '1', 'dealer': {'dealer_id': 'test-dealer'},
        'insurance_history': {'owner_change_count': 3, 'flood_damage': False, 'total_loss': False,
                              'theft': False, 'history_disclosed': True},
        'performance_record': {'record_available': True, 'frame_damage': ['리어 패널 · 교환'],
                               'panel_exchange': ['트렁크리드 · 교환'],
                               'third_party_inspection': {'frame_ok': False}},
    }
    with closing(history.get_connection(server.HISTORY_DB_PATH)) as conn:
        history.record(conn, 'https://example.com/car', source='bobaedream', listing=listing)
        history.record(conn, 'https://example.com/failed', status='failed', reason='판매 종료')
    items = {i['url']: i for i in server.get_history()['items']}
    assert items['https://example.com/failed']['check'] is None
    check = items['https://example.com/car']['check']
    verdicts = {i['key']: i['verdict'] for i in check['items']}
    assert check['overall'] == 'hold'
    assert verdicts['frame_damage'] == 'fail'
    assert verdicts['owner_change'] == verdicts['panel_exchange'] == 'caution'
    assert any('리어 패널 · 교환' in reason for reason in check['hold_reasons'])
    with closing(dealers.get_connection(server.DEALERS_DB_PATH)) as conn:
        assert dealers.list_dealers(conn) == []

