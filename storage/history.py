"""링크 조회 이력과 찜을 로컬 SQLite에 영구 보관한다."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('http 또는 https 매물 링크를 입력해주세요.')
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ''))


def get_connection(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS lookup_history (
            id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL DEFAULT '', listing_json TEXT,
            last_viewed_at TEXT NOT NULL, status TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '', favorite INTEGER NOT NULL DEFAULT 0,
            origin TEXT NOT NULL DEFAULT 'lookup'
        );
        CREATE TABLE IF NOT EXISTS history_migrations (name TEXT PRIMARY KEY);
    ''')
    # 옛 CLI 스냅샷은 웹 조회 이력이라고 표시하지 않는다. 한 번만 가져온다.
    with conn:
        if not conn.execute("SELECT 1 FROM history_migrations WHERE name='snapshots'").fetchone():
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='listing_snapshots'").fetchone():
                rows = conn.execute('SELECT raw_json, scraped_at FROM listing_snapshots ORDER BY scraped_at DESC').fetchall()
                for row in rows:
                    try:
                        listing = json.loads(row['raw_json'])
                        url = normalize_url(listing.get('url', ''))
                    except (ValueError, TypeError, AttributeError):
                        continue
                    conn.execute('''INSERT OR IGNORE INTO lookup_history
                        (url, source, listing_json, last_viewed_at, status, origin)
                        VALUES (?, ?, ?, ?, 'success', 'snapshot')''',
                        (url, listing.get('source', ''), row['raw_json'], row['scraped_at']))
            conn.execute("INSERT INTO history_migrations VALUES ('snapshots')")
    return conn


def record(conn, url: str, *, source: str = '', listing: dict | None = None,
           status: str = 'success', reason: str = '') -> int:
    url = normalize_url(url)
    with conn:
        conn.execute('''INSERT INTO lookup_history
            (url, source, listing_json, last_viewed_at, status, reason)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                source=CASE WHEN excluded.source != '' THEN excluded.source ELSE lookup_history.source END,
                listing_json=COALESCE(excluded.listing_json, lookup_history.listing_json),
                last_viewed_at=excluded.last_viewed_at, status=excluded.status,
                reason=excluded.reason, origin='lookup'
        ''', (url, source, json.dumps(listing, ensure_ascii=False) if listing is not None else None,
              datetime.now(timezone.utc).isoformat(), status, reason))
    return conn.execute('SELECT id FROM lookup_history WHERE url=?', (url,)).fetchone()['id']


def list_history(conn) -> list[dict]:
    result = []
    for row in conn.execute('SELECT * FROM lookup_history ORDER BY last_viewed_at DESC, id DESC'):
        item = dict(row)
        item['listing'] = json.loads(item.pop('listing_json') or 'null')
        item['favorite'] = bool(item['favorite'])
        result.append(item)
    return result


def set_favorite(conn, item_id: int, favorite: bool) -> bool:
    with conn:
        return conn.execute('UPDATE lookup_history SET favorite=? WHERE id=?',
                            (int(favorite), item_id)).rowcount > 0
