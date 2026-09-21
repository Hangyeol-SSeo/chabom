"""딜러 블랙리스트 저장소 (sqlite, 2026-09-18 신설).

**설계 배경**: 사용자가 "안 좋은 매물을 가진 딜러를 다음에 자동으로 걸러달라"고 요청했는데,
이름은 동명이인이 있을 수 있어 키로 쓸 수 없다고 스스로 지적했다. 실측 결과 사업자등록번호는
개별 판매자(특히 개인 판매자) 페이지에 노출되지 않는 경우가 많아 이것도 보편적인 키로 쓸 수
없었다 — 대신 각 사이트가 매물마다 노출하는 **사이트별 판매자 ID**(보배드림의 `sellerID`,
KB차차차의 `dealerNo`)를 1차 키로 쓴다. 이 키는 사이트 내에서는 확실하지만 사이트를 넘어서는
같은 딜러를 자동으로 연결해주지 않으므로, **전화번호**(사용자가 직접 입력)를 보조/참고 매칭
키로 둔다 — 전화번호 일치는 "확정"이 아니라 "확인 권장" 경고로만 쓴다(scoring/checklist.py 참고).
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS dealers (
    source TEXT NOT NULL,
    dealer_key TEXT NOT NULL,
    display_name TEXT,
    phone TEXT,
    region TEXT,
    blacklisted INTEGER NOT NULL DEFAULT 0,
    favorite INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    blacklisted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source, dealer_key)
);

CREATE INDEX IF NOT EXISTS idx_dealers_phone ON dealers(phone);
"""


def normalize_phone(phone: str | None) -> str:
    return re.sub(r"[^\d]", "", phone or "")


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    if 'favorite' not in {r[1] for r in conn.execute('PRAGMA table_info(dealers)')}:
        conn.execute('ALTER TABLE dealers ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0')
        conn.commit()
    return conn


def get_dealer(conn: sqlite3.Connection, source: str, dealer_key: str) -> dict | None:
    if not dealer_key:
        return None
    with closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT source, dealer_key, display_name, phone, region, blacklisted, reason, blacklisted_at, favorite "
            "FROM dealers WHERE source = ? AND dealer_key = ?",
            (source, dealer_key),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "source": row[0], "dealer_key": row[1], "display_name": row[2], "phone": row[3],
        "region": row[4], "blacklisted": bool(row[5]), "reason": row[6], "blacklisted_at": row[7],
        "favorite": bool(row[8]),
    }


def find_by_phone(conn: sqlite3.Connection, phone: str, exclude: tuple[str, str] | None = None) -> list[dict]:
    """이 번호를 쓰는(블랙리스트 등록된) 다른 딜러 레코드를 찾는다 — 참고용 소프트 매칭."""
    norm = normalize_phone(phone)
    if not norm:
        return []
    with closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT source, dealer_key, display_name, reason FROM dealers "
            "WHERE phone = ? AND blacklisted = 1",
            (norm,),
        )
        rows = cur.fetchall()
    results = [{"source": r[0], "dealer_key": r[1], "display_name": r[2], "reason": r[3]} for r in rows]
    if exclude:
        results = [r for r in results if (r["source"], r["dealer_key"]) != exclude]
    return results


def upsert_dealer(
    conn: sqlite3.Connection,
    source: str,
    dealer_key: str,
    display_name: str = "",
    phone: str = "",
    region: str = "",
) -> None:
    if not dealer_key:
        return
    now = datetime.now(timezone.utc).isoformat()
    norm_phone = normalize_phone(phone)
    with closing(conn.cursor()) as cur:
        cur.execute(
            """
            INSERT INTO dealers (source, dealer_key, display_name, phone, region, blacklisted, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(source, dealer_key) DO UPDATE SET
                display_name = COALESCE(NULLIF(excluded.display_name, ''), dealers.display_name),
                phone = COALESCE(NULLIF(excluded.phone, ''), dealers.phone),
                region = COALESCE(NULLIF(excluded.region, ''), dealers.region),
                updated_at = excluded.updated_at
            """,
            (source, dealer_key, display_name, norm_phone, region, now, now),
        )
    conn.commit()


def blacklist_dealer(
    conn: sqlite3.Connection,
    source: str,
    dealer_key: str,
    reason: str,
    display_name: str = "",
    phone: str = "",
    region: str = "",
) -> None:
    if not dealer_key:
        raise ValueError("dealer_key가 비어 있어 블랙리스트에 등록할 수 없습니다")
    now = datetime.now(timezone.utc).isoformat()
    norm_phone = normalize_phone(phone)
    with closing(conn.cursor()) as cur:
        cur.execute(
            """
            INSERT INTO dealers (source, dealer_key, display_name, phone, region, blacklisted, reason, blacklisted_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(source, dealer_key) DO UPDATE SET
                display_name = COALESCE(NULLIF(excluded.display_name, ''), dealers.display_name),
                phone = COALESCE(NULLIF(excluded.phone, ''), dealers.phone),
                region = COALESCE(NULLIF(excluded.region, ''), dealers.region),
                blacklisted = 1,
                favorite = 0,
                reason = excluded.reason,
                blacklisted_at = excluded.blacklisted_at,
                updated_at = excluded.updated_at
            """,
            (source, dealer_key, display_name, norm_phone, region, reason, now, now, now),
        )
    conn.commit()


def unblacklist_dealer(conn: sqlite3.Connection, source: str, dealer_key: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with closing(conn.cursor()) as cur:
        cur.execute(
            "UPDATE dealers SET blacklisted = 0, reason = NULL, blacklisted_at = NULL, updated_at = ? "
            "WHERE source = ? AND dealer_key = ?",
            (now, source, dealer_key),
        )
    conn.commit()


def list_blacklisted(conn: sqlite3.Connection) -> list[dict]:
    with closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT source, dealer_key, display_name, phone, region, reason, blacklisted_at "
            "FROM dealers WHERE blacklisted = 1 ORDER BY blacklisted_at DESC"
        )
        rows = cur.fetchall()
    return [
        {
            "source": r[0], "dealer_key": r[1], "display_name": r[2], "phone": r[3],
            "region": r[4], "reason": r[5], "blacklisted_at": r[6],
        }
        for r in rows
    ]


def list_dealers(conn: sqlite3.Connection) -> list[dict]:
    cursor = conn.execute('SELECT source, dealer_key, display_name, phone, region, '
                          'blacklisted, favorite, reason, blacklisted_at, updated_at '
                          'FROM dealers ORDER BY updated_at DESC, source, dealer_key')
    keys = [column[0] for column in cursor.description]
    rows = [dict(zip(keys, row)) for row in cursor.fetchall()]
    for row in rows:
        row['blacklisted'] = bool(row['blacklisted'])
        row['favorite'] = bool(row['favorite'])
    return rows


def set_favorite(conn: sqlite3.Connection, source: str, dealer_key: str, favorite: bool,
                 display_name: str = '', phone: str = '', region: str = '') -> None:
    if not source.strip() or not dealer_key.strip():
        raise ValueError('사이트와 딜러 ID가 필요합니다.')
    with conn:
        # 상태 확인과 변경을 같은 쓰기 트랜잭션 안에서 처리한다.
        conn.execute('BEGIN IMMEDIATE')
        record = get_dealer(conn, source, dealer_key)
        if favorite and record and record['blacklisted']:
            raise ValueError('블랙리스트를 해제한 후 찜할 수 있습니다.')
        now = datetime.now(timezone.utc).isoformat()
        conn.execute('''INSERT INTO dealers
            (source, dealer_key, display_name, phone, region, favorite, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, dealer_key) DO UPDATE SET
                display_name=COALESCE(NULLIF(excluded.display_name, ''), dealers.display_name),
                phone=COALESCE(NULLIF(excluded.phone, ''), dealers.phone),
                region=COALESCE(NULLIF(excluded.region, ''), dealers.region),
                favorite=excluded.favorite, updated_at=excluded.updated_at''',
            (source, dealer_key, display_name, normalize_phone(phone), region, int(favorite), now, now))
