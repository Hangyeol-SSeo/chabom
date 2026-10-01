"""매물 링크·전화번호 정규화.

예전에는 storage/history.py·storage/dealers.py(SQLite)에 있던 함수다. 웹 버전에서 사용자 데이터는
브라우저가 Firestore에 직접 저장하므로 SQLite 계층은 없어졌고, 서버에는 입력 검증만 남았다.
같은 규칙의 JS 구현이 web/store.js에 있다.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('http 또는 https 매물 링크를 입력해주세요.')
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ''))


def normalize_phone(phone: str | None) -> str:
    return re.sub(r"[^\d]", "", phone or "")
