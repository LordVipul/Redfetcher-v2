import os
import sqlite3
import config


def get_connection():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-16000")
    return conn


def init_db():
    conn = get_connection()
    try:
        had_fts = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='posts_fts'"
        ).fetchone() is not None
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS posts (
                id TEXT PRIMARY KEY,
                url TEXT UNIQUE NOT NULL,
                title TEXT,
                score INTEGER,
                num_comments INTEGER,
                created_utc REAL,
                subreddit TEXT NOT NULL,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_posts_subreddit ON posts(subreddit);
            CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_utc);
            CREATE INDEX IF NOT EXISTS idx_posts_score ON posts(score);
            CREATE INDEX IF NOT EXISTS idx_posts_num_comments ON posts(num_comments);
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_bookmarks_post_id ON bookmarks(post_id);
            CREATE INDEX IF NOT EXISTS idx_bookmarks_created ON bookmarks(created_at);
            CREATE TABLE IF NOT EXISTS scrape_state (
                subreddit TEXT PRIMARY KEY,
                next_start REAL NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
                title,
                content='posts',
                content_rowid='rowid',
                tokenize='trigram'
            );
            CREATE TRIGGER IF NOT EXISTS posts_fts_ai AFTER INSERT ON posts BEGIN
                INSERT INTO posts_fts(rowid, title) VALUES (new.rowid, new.title);
            END;
            CREATE TRIGGER IF NOT EXISTS posts_fts_ad AFTER DELETE ON posts BEGIN
                INSERT INTO posts_fts(posts_fts, rowid, title) VALUES ('delete', old.rowid, old.title);
            END;
            CREATE TRIGGER IF NOT EXISTS posts_fts_au AFTER UPDATE ON posts BEGIN
                INSERT INTO posts_fts(posts_fts, rowid, title) VALUES ('delete', old.rowid, old.title);
                INSERT INTO posts_fts(rowid, title) VALUES (new.rowid, new.title);
            END;
            """
        )
        if not had_fts:
            conn.execute("INSERT INTO posts_fts(posts_fts) VALUES('rebuild')")
        conn.commit()
        try:
            os.chmod(config.DB_PATH, 0o600)
        except OSError:
            pass  # e.g. Windows: chmod is limited to the read-only flag
    finally:
        conn.close()


def _title_search_clause(alias, q):
    """Return (clause, param) for title search, or (None, None) if q is empty.

    Uses FTS5 trigram (substring, index-backed) for q length >= 3;
    falls back to LIKE for shorter queries.
    """
    if not q:
        return None, None
    if len(q) >= 3:
        match = '"' + q.replace('"', '""') + '"'
        return f" AND {alias}rowid IN (SELECT rowid FROM posts_fts WHERE posts_fts MATCH ?)", match
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f" AND {alias}title LIKE ? ESCAPE '\\'", f"%{escaped}%"


def insert_posts(posts):
    if not posts:
        return 0
    conn = get_connection()
    try:
        cursor = conn.executemany(
            "INSERT OR IGNORE INTO posts (id, url, title, score, num_comments, created_utc, subreddit) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(p["id"], p["url"], p["title"], p["score"], p["num_comments"], p["created_utc"], p["subreddit"]) for p in posts],
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def get_max_created_utc(subreddit):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT MAX(created_utc) FROM posts WHERE subreddit = ?", (subreddit,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def get_scrape_state(subreddit):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT next_start FROM scrape_state WHERE subreddit = ?", (subreddit,)
        ).fetchone()
        return row["next_start"] if row else None
    finally:
        conn.close()


def advance_scrape_state(subreddit, next_start):
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO scrape_state (subreddit, next_start) VALUES (?, ?)
            ON CONFLICT(subreddit) DO UPDATE SET
                next_start = CASE
                    WHEN scrape_state.next_start < excluded.next_start
                    THEN excluded.next_start
                    ELSE scrape_state.next_start
                END,
                updated_at = CURRENT_TIMESTAMP
            """,
            (subreddit, next_start),
        )
        conn.commit()
    finally:
        conn.close()


def get_stats():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT subreddit, COUNT(*) as count, MAX(created_utc) as last_updated FROM posts GROUP BY subreddit ORDER BY count DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_posts(subreddit=None, start_date=None, end_date=None, sort="score_desc", limit=100, q=None, page=1):
    conn = get_connection()
    try:
        where = " WHERE 1=1"
        params = []

        if subreddit:
            where += " AND subreddit = ?"
            params.append(subreddit)
        if start_date:
            where += " AND created_utc >= ?"
            params.append(start_date)
        if end_date:
            where += " AND created_utc <= ?"
            params.append(end_date)
        clause, param = _title_search_clause("", q)
        if clause:
            where += clause
            params.append(param)

        sort_map = {
            "score_desc": "score DESC",
            "score_asc": "score ASC",
            "comments_desc": "num_comments DESC",
            "comments_asc": "num_comments ASC",
            "newest": "created_utc DESC",
            "oldest": "created_utc ASC",
        }
        if sort not in sort_map:
            raise ValueError(f"invalid sort: {sort!r}")
        order = sort_map[sort]
        offset = (page - 1) * limit

        query = (
            f"SELECT *, COUNT(*) OVER() AS _total FROM posts{where} "
            f"ORDER BY {order} LIMIT ? OFFSET ?"
        )
        rows = conn.execute(query, params + [limit, offset]).fetchall()

        if rows:
            total = rows[0]["_total"]
            results = []
            for r in rows:
                d = dict(r)
                d.pop("_total", None)
                results.append(d)
        else:
            total = conn.execute(f"SELECT COUNT(*) FROM posts{where}", params).fetchone()[0]
            results = []

        return results, total
    finally:
        conn.close()


def get_total_count(subreddit=None):
    conn = get_connection()
    try:
        if subreddit:
            row = conn.execute("SELECT COUNT(*) FROM posts WHERE subreddit = ?", (subreddit,)).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM posts").fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def get_all_subreddits():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT DISTINCT subreddit FROM posts ORDER BY subreddit").fetchall()
        return [r["subreddit"] for r in rows]
    finally:
        conn.close()


# ── Bookmarks ──────────────────────────────────────────

def add_bookmark(post_id):
    conn = get_connection()
    try:
        conn.execute("INSERT OR IGNORE INTO bookmarks (post_id) VALUES (?)", (post_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def remove_bookmark(post_id):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM bookmarks WHERE post_id = ?", (post_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def is_bookmarked(post_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT 1 FROM bookmarks WHERE post_id = ?", (post_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


def get_bookmark_ids_for(post_ids):
    """Return the subset of post_ids that are bookmarked."""
    if not post_ids:
        return []
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(post_ids))
        rows = conn.execute(
            f"SELECT post_id FROM bookmarks WHERE post_id IN ({placeholders})",
            list(post_ids),
        ).fetchall()
        return [r["post_id"] for r in rows]
    finally:
        conn.close()


def get_bookmarks(limit=50, offset=0, sort="oldest", subreddit=None, start_date=None, end_date=None, q=None):
    conn = get_connection()
    try:
        where = """
            FROM posts p
            INNER JOIN bookmarks b ON p.id = b.post_id
            WHERE 1=1
        """
        params = []

        if subreddit:
            where += " AND p.subreddit = ?"
            params.append(subreddit)
        if start_date:
            where += " AND p.created_utc >= ?"
            params.append(start_date)
        if end_date:
            where += " AND p.created_utc <= ?"
            params.append(end_date)
        clause, param = _title_search_clause("p.", q)
        if clause:
            where += clause
            params.append(param)

        sort_map = {
            "oldest": "b.created_at ASC",
            "newest": "b.created_at DESC",
            "score_desc": "p.score DESC",
            "score_asc": "p.score ASC",
            "comments_desc": "p.num_comments DESC",
            "comments_asc": "p.num_comments ASC",
        }
        if sort not in sort_map:
            raise ValueError(f"invalid sort: {sort!r}")
        order = sort_map[sort]

        query = (
            "SELECT p.*, b.created_at as bookmarked_at, COUNT(*) OVER() AS _total "
            f"{where} ORDER BY {order} LIMIT ? OFFSET ?"
        )
        rows = conn.execute(query, params + [limit, offset]).fetchall()

        if rows:
            total = rows[0]["_total"]
            results = []
            for r in rows:
                d = dict(r)
                d.pop("_total", None)
                results.append(d)
        else:
            total = conn.execute(
                "SELECT COUNT(*) " + where, params
            ).fetchone()[0]
            results = []

        return results, total
    finally:
        conn.close()


def get_bookmarks_count(subreddit=None, start_date=None, end_date=None, q=None):
    conn = get_connection()
    try:
        query = "SELECT COUNT(*) FROM bookmarks b INNER JOIN posts p ON p.id = b.post_id WHERE 1=1"
        params = []
        if subreddit:
            query += " AND p.subreddit = ?"
            params.append(subreddit)
        if start_date:
            query += " AND p.created_utc >= ?"
            params.append(start_date)
        if end_date:
            query += " AND p.created_utc <= ?"
            params.append(end_date)
        clause, param = _title_search_clause("p.", q)
        if clause:
            query += clause
            params.append(param)
        total = conn.execute(query, params).fetchone()[0]
        return total
    finally:
        conn.close()
