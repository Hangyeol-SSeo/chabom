"""파서 테스트의 생성 링크도 예약된 예시 도메인만 사용한다."""
import pytest


@pytest.fixture(autouse=True)
def synthetic_adapter_hosts(monkeypatch):
    from crawler.adapters import bobaedream_adapter, encar_detail_adapter

    monkeypatch.setattr(bobaedream_adapter, 'BASE_URL', 'https://bobaedream.example.com')
    monkeypatch.setattr(encar_detail_adapter, 'PHOTO_CDN', 'https://images.example.com/carpicture')
