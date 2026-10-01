"""API 서버(server.py) 테스트 — Firebase 대신 가짜 gateway로 인증·허용 목록·한도를 흉내 낸다."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import server
from api.auth import user_from_claims
from api.validation import normalize_phone, normalize_url

GOOGLE_CLAIMS = {'uid': 'user-a', 'email': 'Tester@Example.com', 'email_verified': True,
                 'firebase': {'sign_in_provider': 'google.com'}}


class FakeGateway:
    def __init__(self, claims=None, allowed=('tester@example.com',), quota=10):
        self.claims = claims or GOOGLE_CLAIMS
        self.allowed = set(allowed)
        self.quota = quota
        self.consumed = []

    def verify_token(self, token):
        if token != 'good-token':
            raise HTTPException(status_code=401, detail='로그인이 만료되었습니다.')
        return self.claims

    def is_allowed(self, email):
        return email in self.allowed

    def consume_lookup(self, uid, limit):
        if len(self.consumed) >= self.quota:
            return False
        self.consumed.append(uid)
        return True


@pytest.fixture
def client(monkeypatch):
    fake = FakeGateway()
    monkeypatch.setattr(server, 'gateway', fake)
    # 테스트에는 실제 매물 사이트 주소를 쓰지 않는다(test_fixture_privacy.py).
    monkeypatch.setattr(server, '_SOURCE_HOSTS', {'encar.example.com': ('encar', True),
                                                  'bobaedream.example.com': ('bobaedream', True)})
    with TestClient(server.app) as test_client:
        test_client.fake = fake
        yield test_client


AUTH = {'Authorization': 'Bearer good-token'}


def test_requires_login(client):
    assert client.post('/api/verify', json={'listing': {}}).status_code == 401
    assert client.post('/api/verify', json={'listing': {}}, headers={'Authorization': 'Bearer bad'}).status_code == 401


def test_rejects_accounts_not_on_allowlist(client):
    client.fake.allowed = set()
    response = client.post('/api/verify', json={'listing': {}}, headers=AUTH)
    assert response.status_code == 403
    assert 'tester@example.com' in response.json()['detail']


@pytest.mark.parametrize('claims', [
    {**GOOGLE_CLAIMS, 'email_verified': False},
    {**GOOGLE_CLAIMS, 'firebase': {'sign_in_provider': 'password'}},
    {**GOOGLE_CLAIMS, 'firebase': {'sign_in_provider': 'anonymous'}},
    {'uid': 'k', 'firebase': {'sign_in_provider': 'oidc.kakao'}},
])
def test_rejects_untrusted_claims(claims):
    with pytest.raises(HTTPException) as error:
        user_from_claims(claims)
    assert error.value.status_code == 403


def test_kakao_account_uses_lowercased_email():
    user = user_from_claims({'uid': 'k', 'email': 'Kakao@Example.com', 'firebase': {'sign_in_provider': 'oidc.kakao'}})
    assert user.email == 'kakao@example.com'


def test_lookup_returns_listing_without_storing(client, monkeypatch):
    calls = []

    def fake_lookup(url, source):
        calls.append((url, source))
        return {'ok': True, 'listing': {'source': source, 'vehicle': {'model': '모닝'}}}

    monkeypatch.setattr(server, '_lookup', fake_lookup)
    response = client.post('/api/lookup', json={'url': 'HTTPS://FEM.ENCAR.EXAMPLE.COM/cars/detail/1#photo'}, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body['ok'] and body['url'] == 'https://fem.encar.example.com/cars/detail/1'
    assert body['source'] == 'encar' and body['listing']['vehicle']['model'] == '모닝'
    assert calls == [('https://fem.encar.example.com/cars/detail/1', 'encar')]
    assert client.fake.consumed == ['user-a']


def test_lookup_unknown_site_does_not_use_quota(client):
    response = client.post('/api/lookup', json={'url': 'https://example.com/car'}, headers=AUTH)
    assert response.status_code == 200
    assert response.json()['ok'] is False
    assert client.fake.consumed == []


def test_lookup_rejects_lookalike_host(client):
    response = client.post('/api/lookup', json={'url': 'https://notencar.example.com/cars/detail/1'}, headers=AUTH)
    assert response.json()['ok'] is False and client.fake.consumed == []


def test_lookup_daily_limit(client, monkeypatch):
    monkeypatch.setattr(server, '_lookup', lambda url, source: {'ok': False, 'reason': '테스트'})
    client.fake.quota = 1
    assert client.post('/api/lookup', json={'url': 'https://www.bobaedream.example.com/a'}, headers=AUTH).status_code == 200
    response = client.post('/api/lookup', json={'url': 'https://www.bobaedream.example.com/a'}, headers=AUTH)
    assert response.status_code == 429


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///tmp/a', 'https://', 'https://user:pass@example.com'])
def test_lookup_rejects_unsafe_links(client, url):
    assert client.post('/api/lookup', json={'url': url}, headers=AUTH).status_code == 422


def test_verify_manual_listing_holds_when_unchecked(client):
    response = client.post('/api/verify', json={'listing': {'url': 'https://example.com/car', 'vehicle': {'model': '모닝'}}},
                           headers=AUTH)
    assert response.status_code == 200
    assert response.json()['overall'] == 'hold'
    assert response.json()['listing']['vehicle']['model'] == '모닝'


def test_verify_uses_dealer_context(client):
    listing = {'source': 'encar', 'dealer': {'dealer_id': 'dealer-1', 'phone': '010-0000-0001'}}
    context = {'record': {'blacklisted': True, 'reason': '기록 불일치', 'blacklisted_at': '2026-10-01'},
               'phone_matches': [{'source': 'encar', 'dealer_key': 'dealer-1', 'reason': '자기 자신'},
                                 {'source': 'kcar', 'dealer_key': 'dealer-9', 'reason': '다른 딜러'}]}
    body = client.post('/api/verify', json={'listing': listing, 'dealer_context': context}, headers=AUTH).json()
    assert body['dealer_status']['blacklisted'] is True
    assert body['dealer_status']['reason'] == '기록 불일치'
    assert [m['dealer_key'] for m in body['dealer_status']['phone_matches']] == ['dealer-9']
    dealer_item = next(i for i in body['items'] if i['key'] == 'dealer_blacklist')
    assert dealer_item['verdict'] == 'fail'


def test_verify_without_dealer_context_treats_dealer_as_clean(client):
    body = client.post('/api/verify', json={'listing': {'dealer': {'dealer_id': 'd'}}}, headers=AUTH).json()
    assert body['dealer_status']['known'] is True and body['dealer_status']['blacklisted'] is False


def test_static_files_are_not_served_outside_dev_mode(client):
    assert client.get('/').status_code == 404


def test_normalizers():
    assert normalize_url(' https://Example.COM/car?a=1#x ') == 'https://example.com/car?a=1'
    assert normalize_phone('010-0000-0000') == '01000000000'
    assert normalize_phone(None) == ''
