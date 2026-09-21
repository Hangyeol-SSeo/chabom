"""매물 스냅샷 저장소 (sqlite). 재크롤링 시 가격 변동·매물 소멸 이력을 추적한다."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from normalizer.schema import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS listing_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id TEXT NOT NULL,
    source TEXT NOT NULL,
    scraped_at TEXT NOT NULL,
    price_krw INTEGER,
    risk_score INTEGER,
    value_score REAL,
    raw_json TEXT NOT NULL,
    UNIQUE(listing_id, source, scraped_at)
);

CREATE INDEX IF NOT EXISTS idx_listing_snapshots_listing
    ON listing_snapshots(listing_id, source);
"""


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    return conn


def insert_snapshot(
    conn: sqlite3.Connection,
    listing: Listing,
    risk_score: int | None = None,
    value_score: float | None = None,
    scraped_at: str | None = None,
) -> None:
    scraped_at = scraped_at or datetime.now(timezone.utc).isoformat()
    with closing(conn.cursor()) as cur:
        cur.execute(
            """
            INSERT OR IGNORE INTO listing_snapshots
                (listing_id, source, scraped_at, price_krw, risk_score, value_score, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                listing.listing_id,
                listing.source,
                scraped_at,
                listing.vehicle.price_krw,
                risk_score,
                value_score,
                json.dumps(asdict(listing), ensure_ascii=False),
            ),
        )
    conn.commit()


def get_price_history(conn: sqlite3.Connection, listing_id: str, source: str) -> list[tuple[str, int | None]]:
    with closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT scraped_at, price_krw FROM listing_snapshots
            WHERE listing_id = ? AND source = ?
            ORDER BY scraped_at ASC
            """,
            (listing_id, source),
        )
        return cur.fetchall()


def get_latest_snapshots(conn: sqlite3.Connection, source: str | None = None) -> list[dict]:
    query = """
        SELECT s.listing_id, s.source, s.scraped_at, s.price_krw, s.risk_score, s.value_score, s.raw_json
        FROM listing_snapshots s
        INNER JOIN (
            SELECT listing_id, source, MAX(scraped_at) AS max_scraped_at
            FROM listing_snapshots
            {where}
            GROUP BY listing_id, source
        ) latest
        ON s.listing_id = latest.listing_id
           AND s.source = latest.source
           AND s.scraped_at = latest.max_scraped_at
    """
    params: tuple = ()
    where_clause = ""
    if source:
        where_clause = "WHERE source = ?"
        params = (source,)
    with closing(conn.cursor()) as cur:
        cur.execute(query.format(where=where_clause), params)
        rows = cur.fetchall()
    return [
        {
            "listing_id": r[0],
            "source": r[1],
            "scraped_at": r[2],
            "price_krw": r[3],
            "risk_score": r[4],
            "value_score": r[5],
            "raw": json.loads(r[6]),
        }
        for r in rows
    ]
