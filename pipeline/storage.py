"""SQLite 영구 저장소 모듈.

테이블 구성
- raw_articles : 수집한 원본 그대로 (수집 시각, 소스, 수집 방법 포함)
- articles     : 정제된(clean) 뉴스 + 요약/감성 결과
- analyses     : AI 인사이트 분석 결과
"""
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_articles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,          -- 예: yonhap
    method      TEXT NOT NULL,          -- rss / crawl
    url         TEXT,
    payload     TEXT NOT NULL,          -- 원본 데이터(JSON 문자열)
    fetched_at  TEXT NOT NULL,          -- 수집 시각
    processed   INTEGER NOT NULL DEFAULT 0  -- clean 단계 처리 여부
);

CREATE TABLE IF NOT EXISTS articles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_id        INTEGER REFERENCES raw_articles(id),
    url           TEXT NOT NULL UNIQUE, -- 중복 판단 기준
    title         TEXT NOT NULL,
    content       TEXT,
    category      TEXT NOT NULL DEFAULT '미분류',
    source        TEXT NOT NULL,
    method        TEXT NOT NULL,
    published_at  TEXT,                 -- YYYY-MM-DD HH:MM:SS 로 통일
    collected_at  TEXT NOT NULL,
    summary       TEXT,
    summarized_at TEXT,
    sentiment     TEXT,                 -- 보너스: 긍정/부정/중립
    status        TEXT NOT NULL DEFAULT 'cleaned',  -- cleaned / summarized
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date_from     TEXT,
    date_to       TEXT,
    category      TEXT,
    article_count INTEGER NOT NULL,
    result        TEXT NOT NULL,        -- 분석 결과(JSON 문자열)
    model         TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_articles_category  ON articles(category);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_status    ON articles(status);
"""

ARTICLE_FIELDS = ("title", "content", "category", "source", "method",
                  "published_at", "collected_at")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Storage:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row  # 결과를 dict 처럼 사용
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------- raw ----------
    def insert_raw(self, source: str, method: str, url: str, payload: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO raw_articles (source, method, url, payload, fetched_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (source, method, url, json.dumps(payload, ensure_ascii=False), now_str()),
        )
        self.conn.commit()
        return cur.lastrowid

    def raw_exists(self, url: str, method: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM raw_articles WHERE url = ? AND method = ? LIMIT 1", (url, method)
        ).fetchone()
        return row is not None

    def get_unprocessed_raw(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM raw_articles WHERE processed = 0 ORDER BY id"
        ).fetchall()
        result = []
        for r in rows:
            item = dict(r)
            item["payload"] = json.loads(item["payload"])
            result.append(item)
        return result

    def get_latest_raw(self, url: str, method: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM raw_articles WHERE url = ? AND method = ? ORDER BY id DESC LIMIT 1",
            (url, method),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        return item

    def reset_raw_processed(self) -> int:
        cur = self.conn.execute("UPDATE raw_articles SET processed = 0")
        self.conn.commit()
        return cur.rowcount

    def mark_raw_processed(self, raw_id: int) -> None:
        self.conn.execute("UPDATE raw_articles SET processed = 1 WHERE id = ?", (raw_id,))
        self.conn.commit()

    # ---------- clean ----------
    def save_article(self, article: dict, policy: str = "skip") -> str:
        """정제된 기사 저장. 반환값: 'inserted' / 'updated' / 'skipped'."""
        existing = self.conn.execute(
            "SELECT id FROM articles WHERE url = ?", (article["url"],)
        ).fetchone()
        ts = now_str()

        if existing is None:
            self.conn.execute(
                "INSERT INTO articles (raw_id, url, title, content, category, source, method, "
                "published_at, collected_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (article.get("raw_id"), article["url"],
                 *(article.get(f) for f in ARTICLE_FIELDS), ts, ts),
            )
            self.conn.commit()
            return "inserted"

        if policy == "upsert":
            sets = ", ".join(f"{f} = ?" for f in ARTICLE_FIELDS)
            self.conn.execute(
                f"UPDATE articles SET {sets}, raw_id = ?, updated_at = ? WHERE id = ?",
                (*(article.get(f) for f in ARTICLE_FIELDS),
                 article.get("raw_id"), ts, existing["id"]),
            )
            self.conn.commit()
            return "updated"

        return "skipped"

    def get_articles(self, category=None, date_from=None, date_to=None,
                     status=None, keyword=None, ids=None,
                     limit=None, offset=0) -> list[dict]:
        """조건에 맞는 기사 목록. 날짜는 'YYYY-MM-DD' 형식."""
        where, params = [], []
        if category:
            where.append("category = ?"); params.append(category)
        if date_from:
            where.append("date(published_at) >= date(?)"); params.append(date_from)
        if date_to:
            where.append("date(published_at) <= date(?)"); params.append(date_to)
        if status:
            where.append("status = ?"); params.append(status)
        if keyword:
            where.append("(title LIKE ? OR content LIKE ?)")
            params += [f"%{keyword}%", f"%{keyword}%"]
        if ids:
            where.append(f"id IN ({','.join('?' * len(ids))})"); params += list(ids)

        sql = "SELECT * FROM articles"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY published_at DESC, id DESC"
        if limit:
            sql += " LIMIT ? OFFSET ?"; params += [limit, offset]
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_article(self, article_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        return dict(row) if row else None

    def update_summary(self, article_id: int, summary: str) -> None:
        ts = now_str()
        self.conn.execute(
            "UPDATE articles SET summary = ?, summarized_at = ?, status = 'summarized', "
            "updated_at = ? WHERE id = ?",
            (summary, ts, ts, article_id),
        )
        self.conn.commit()

    def update_sentiment(self, article_id: int, sentiment: str) -> None:
        self.conn.execute(
            "UPDATE articles SET sentiment = ?, updated_at = ? WHERE id = ?",
            (sentiment, now_str(), article_id),
        )
        self.conn.commit()

    # ---------- analyses ----------
    def save_analysis(self, result: dict, article_count: int, model: str,
                      date_from=None, date_to=None, category=None) -> int:
        cur = self.conn.execute(
            "INSERT INTO analyses (date_from, date_to, category, article_count, result, "
            "model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (date_from, date_to, category, article_count,
             json.dumps(result, ensure_ascii=False), model, now_str()),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_analysis(self, analysis_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
        return self._analysis_row(row)

    def list_analyses(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, date_from, date_to, category, article_count, model, created_at "
            "FROM analyses ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_latest_analysis(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM analyses ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return self._analysis_row(row)

    @staticmethod
    def _analysis_row(row) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        item["result"] = json.loads(item["result"])
        return item

    # ---------- 통계 ----------
    def count(self, table: str) -> int:
        if table not in ("raw_articles", "articles", "analyses"):
            raise ValueError(f"알 수 없는 테이블: {table}")
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
