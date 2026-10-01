"""로컬 버전의 SQLite 데이터(data/listings.db, data/dealers.db)를 웹 버전 사용자 계정으로 옮긴다.

    gcloud auth application-default login        # 최초 1회
    python -m scripts.import_local_data --project <프로젝트 ID> --email me@gmail.com --dry-run
    python -m scripts.import_local_data --project <프로젝트 ID> --email me@gmail.com

웹에서 해당 이메일로 한 번 로그인해 계정이 만들어진 뒤에 실행한다. 같은 링크·딜러가 이미 있으면
덮어쓰므로, 웹에서 쓰기 시작하기 전에 한 번만 실행하는 것을 권한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from api.validation import normalize_url

ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PORTS = {'http': 80, 'https': 443}


def web_url(url: str) -> str:
    """web/store.js의 normalizeURL(브라우저 URL.href)과 같은 문자열을 만든다 — 문서 ID가 같아야 한다."""
    parts = urlsplit(normalize_url(url))
    host = parts.hostname or ''
    netloc = host if parts.port in (None, _DEFAULT_PORTS.get(parts.scheme)) else f'{host}:{parts.port}'
    path = quote(parts.path or '/', safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="/?%:@!$&'()*+,;=-._~")
    return urlunsplit((parts.scheme, netloc, path, query, ''))


def history_docs(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    docs = {}
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lookup_history'").fetchone():
            return {}
        for row in conn.execute('SELECT * FROM lookup_history ORDER BY last_viewed_at'):
            try:
                url = web_url(row['url'])
            except ValueError:
                continue
            docs[hashlib.sha256(url.encode()).hexdigest()] = {
                'url': url, 'source': row['source'] or '',
                'listing': json.loads(row['listing_json']) if row['listing_json'] else None,
                'last_viewed_at': row['last_viewed_at'], 'status': row['status'] if row['status'] in ('success', 'failed') else 'success',
                'reason': row['reason'] or '', 'favorite': bool(row['favorite']),
                'origin': row['origin'] if row['origin'] in ('lookup', 'snapshot') else 'lookup',
            }
    return docs


def dealer_docs(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    docs = {}
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        columns = {r[1] for r in conn.execute('PRAGMA table_info(dealers)')}
        for row in conn.execute('SELECT * FROM dealers'):
            blacklisted = bool(row['blacklisted'])
            key = quote(row['dealer_key'], safe="!'()*")  # web/store.js의 encodeURIComponent와 같게
            docs[f"{row['source']}__{key}"] = {
                'source': row['source'], 'dealer_key': row['dealer_key'],
                'display_name': row['display_name'] or '', 'phone': row['phone'] or '', 'region': row['region'] or '',
                'blacklisted': blacklisted,
                'favorite': bool(row['favorite']) and not blacklisted if 'favorite' in columns else False,
                'reason': (row['reason'] or '(사유 미기재)') if blacklisted else '',
                'blacklisted_at': (row['blacklisted_at'] or '') if blacklisted else '',
                'created_at': row['created_at'] or now, 'updated_at': row['updated_at'] or now,
            }
    return docs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='로컬 SQLite 데이터를 웹 계정으로 옮기기')
    parser.add_argument('--project', required=True)
    parser.add_argument('--email', required=True, help='웹에서 한 번 로그인한 계정 이메일')
    parser.add_argument('--history-db', type=Path, default=ROOT / 'data/listings.db')
    parser.add_argument('--dealers-db', type=Path, default=ROOT / 'data/dealers.db')
    parser.add_argument('--dry-run', action='store_true', help='옮길 개수만 확인')
    args = parser.parse_args(argv)

    history = history_docs(args.history_db)
    dealers = dealer_docs(args.dealers_db)
    print(f'차량 {len(history)}건, 딜러 {len(dealers)}명')
    if args.dry_run:
        return 0

    os.environ['GOOGLE_CLOUD_PROJECT'] = args.project
    from firebase_admin import auth

    from api.auth import FirebaseGateway

    gateway = FirebaseGateway()
    db = gateway.firestore_client()
    uid = auth.get_user_by_email(args.email.strip().lower(), app=gateway._ensure()).uid
    batch, pending = db.batch(), 0
    for collection, docs in (('history', history), ('dealers', dealers)):
        for doc_id, data in docs.items():
            batch.set(db.document(f'users/{uid}/{collection}/{doc_id}'), data)
            pending += 1
            if pending == 400:
                batch.commit()
                batch, pending = db.batch(), 0
    if pending:
        batch.commit()
    print(f'{args.email} 계정으로 옮겼습니다.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
