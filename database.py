import os
import sqlite3
import time

import config


def get_connection():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-16000")
    return conn


def migrate_bookmarks_foreign_key(conn):
    if conn.execute("PRAGMA foreign_key_list(bookmarks)").fetchall():
        return
    conn.execute("DELETE FROM bookmarks WHERE post_id NOT IN (SELECT id FROM posts)")
    conn.execute("DROP INDEX IF EXISTS idx_bookmarks_post_id")
    conn.execute("DROP INDEX IF EXISTS idx_bookmarks_created")
    conn.execute("ALTER TABLE bookmarks RENAME TO bookmarks_legacy")
    conn.execute(
        """
        CREATE TABLE bookmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "INSERT INTO bookmarks (id, post_id, created_at) "
        "SELECT id, post_id, created_at FROM bookmarks_legacy"
    )
    conn.execute("DROP TABLE bookmarks_legacy")
    conn.execute("CREATE INDEX idx_bookmarks_post_id ON bookmarks(post_id)")
    conn.execute("CREATE INDEX idx_bookmarks_created ON bookmarks(created_at)")


def init_db():
    conn = get_connection()
    try:
        had_fts = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='posts_fts'"
        ).fetchone() is not None
        had_hidden = conn.execute(
            "SELECT 1 FROM pragma_table_info('posts') WHERE name='hidden'"
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
                hidden TEXT NOT NULL DEFAULT 'false',
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_posts_subreddit ON posts(subreddit);
            CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_utc);
            CREATE INDEX IF NOT EXISTS idx_posts_score ON posts(score);
            CREATE INDEX IF NOT EXISTS idx_posts_num_comments ON posts(num_comments);
            CREATE INDEX IF NOT EXISTS idx_posts_subreddit_created ON posts(subreddit, created_utc);
            CREATE INDEX IF NOT EXISTS idx_posts_subreddit_score ON posts(subreddit, score);
            CREATE INDEX IF NOT EXISTS idx_posts_subreddit_score_cmt ON posts(subreddit, score, num_comments);
            CREATE INDEX IF NOT EXISTS idx_posts_score_cmt ON posts(score, num_comments);
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_bookmarks_post_id ON bookmarks(post_id);
            CREATE INDEX IF NOT EXISTS idx_bookmarks_created ON bookmarks(created_at);
            CREATE TABLE IF NOT EXISTS scrape_state (
                subreddit TEXT PRIMARY KEY,
                next_start REAL NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS link_checks (
                url TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        migrate_bookmarks_foreign_key(conn)
        if not had_hidden:
            conn.execute("ALTER TABLE posts ADD COLUMN hidden TEXT NOT NULL DEFAULT 'false'")
        _ensure_fts_update_guard(conn)
        if not had_fts:
            conn.execute("INSERT INTO posts_fts(posts_fts) VALUES('rebuild')")
        conn.commit()
        try:
            os.chmod(config.DB_PATH, 0o600)
        except OSError:
            pass  # e.g. Windows: chmod is limited to the read-only flag
    finally:
        conn.close()


def _ensure_fts_update_guard(conn):
    """Recreate posts_fts_au so hidden-flag updates don't churn the FTS index.

    The AFTER UPDATE trigger rewrites the title into posts_fts on every
    update; bulk maintenance updates set only `hidden`, leaving `title`
    untouched, so the rewrite is skipped for unchanged titles.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = 'posts_fts_au'"
    ).fetchone()
    if row is None or "old.title IS NOT new.title" in row[0]:
        return
    conn.execute("DROP TRIGGER posts_fts_au")
    conn.execute(
        """
        CREATE TRIGGER posts_fts_au AFTER UPDATE ON posts
        WHEN old.title IS NOT new.title
        BEGIN
            INSERT INTO posts_fts(posts_fts, rowid, title) VALUES ('delete', old.rowid, old.title);
            INSERT INTO posts_fts(rowid, title) VALUES (new.rowid, new.title);
        END;
        """
    )


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
            "SELECT subreddit, COUNT(*) as count, MAX(created_utc) as last_updated "
            "FROM posts WHERE hidden = 'false' GROUP BY subreddit ORDER BY count DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _min_filter_clauses(alias, min_score, min_comments):
    """Return (clauses, params) for minimum score/comment thresholds.

    None means "no filter" and emits no clause. 0 is a real filter and emits
    `>= 0`, which differs from no filter whenever the column holds negatives.
    """
    clauses = []
    params = []
    if min_score is not None:
        clauses.append(f" AND {alias}score >= ?")
        params.append(min_score)
    if min_comments is not None:
        clauses.append(f" AND {alias}num_comments >= ?")
        params.append(min_comments)
    return clauses, params


def get_posts(subreddit=None, start_date=None, end_date=None, sort="oldest", limit=100, q=None, page=1, min_score=None, min_comments=None, subreddits=None, offset=None):
    conn = get_connection()
    try:
        # Non-subreddit filters, shared by the count query and every row query.
        # Bare " AND …" clauses; each query supplies its own WHERE 1=1.
        filters = " AND hidden = 'false'"
        params = []
        if start_date:
            filters += " AND created_utc >= ?"
            params.append(start_date)
        if end_date:
            filters += " AND created_utc < ?"
            params.append(end_date)
        clauses, clause_params = _min_filter_clauses("", min_score, min_comments)
        for c in clauses:
            filters += c
        params.extend(clause_params)
        clause, param = _title_search_clause("", q)
        if clause:
            filters += clause
            params.append(param)

        sort_map = {
            "score_desc": ("score", "DESC"),
            "score_asc": ("score", "ASC"),
            "comments_desc": ("num_comments", "DESC"),
            "comments_asc": ("num_comments", "ASC"),
            "newest": ("created_utc", "DESC"),
            "oldest": ("created_utc", "ASC"),
        }
        if sort not in sort_map:
            raise ValueError(f"invalid sort: {sort!r}")
        col, direction = sort_map[sort]
        # `offset` overrides the page-derived offset (single-row top-ups after
        # hides, which must read past the page window without shifting pages).
        if offset is None:
            offset = (page - 1) * limit

        if subreddit:
            total = conn.execute(
                f"SELECT COUNT(*) FROM posts WHERE 1=1 AND subreddit = ?{filters}",
                [subreddit] + params,
            ).fetchone()[0]
        elif subreddits:
            placeholders = ",".join("?" * len(subreddits))
            total = conn.execute(
                f"SELECT COUNT(*) FROM posts WHERE 1=1 AND subreddit IN ({placeholders}){filters}",
                list(subreddits) + params,
            ).fetchone()[0]
        else:
            total = conn.execute(f"SELECT COUNT(*) FROM posts WHERE 1=1{filters}", params).fetchone()[0]

        # Group queries: one indexed per-subreddit branch, merged and sorted in
        # SQL. A single IN-list makes SQLite temp-b-tree-sort every matching row
        # (0.8s on the AI group); branches stay on the (subreddit, <sort col>)
        # index (~5ms). Shallow pages only — past a deep offset the per-branch
        # cutoffs fetch more rows than the plain scan costs, so deep pages fall
        # back to the IN-list below. id breaks ties so pages are stable.
        if subreddits and len(subreddits) > 1 and limit + offset <= 5000:
            branches, branch_params = [], []
            for sub in subreddits:
                branches.append(
                    f"SELECT * FROM (SELECT * FROM posts WHERE 1=1 AND subreddit = ?{filters} "
                    f"ORDER BY {col} {direction}, id ASC LIMIT ?)"
                )
                branch_params.extend([sub] + params + [limit + offset])
            sql = (
                " UNION ALL ".join(branches)
                + f" ORDER BY {col} {direction}, id ASC LIMIT ? OFFSET ?"
            )
            rows = conn.execute(sql, branch_params + [limit, offset]).fetchall()
            return [dict(row) for row in rows], total

        if subreddits:
            placeholders = ",".join("?" * len(subreddits))
            rows = conn.execute(
                f"SELECT * FROM posts WHERE 1=1 AND subreddit IN ({placeholders}){filters} "
                f"ORDER BY {col} {direction}, id ASC LIMIT ? OFFSET ?",
                list(subreddits) + params + [limit, offset],
            ).fetchall()
        elif subreddit:
            rows = conn.execute(
                f"SELECT * FROM posts WHERE 1=1 AND subreddit = ?{filters} "
                f"ORDER BY {col} {direction}, id ASC LIMIT ? OFFSET ?",
                [subreddit] + params + [limit, offset],
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM posts WHERE 1=1{filters} ORDER BY {col} {direction}, id ASC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
        return [dict(row) for row in rows], total
    finally:
        conn.close()


def get_all_subreddits():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT subreddit FROM posts WHERE hidden = 'false' ORDER BY subreddit"
        ).fetchall()
        return [r["subreddit"] for r in rows]
    finally:
        conn.close()


# ── Hide / curation ───────────────────────────────

def hide_post(post_id):
    """Set hidden='true' for one post. Returns False when the post doesn't exist."""
    conn = get_connection()
    try:
        exists = conn.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone()
        if exists is None:
            return False
        # Idempotent: already-hidden rows are left alone (no churn).
        conn.execute(
            "UPDATE posts SET hidden = 'true' WHERE id = ? AND hidden = 'false'",
            (post_id,),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def unhide_post(post_id):
    """Reverse a hide: set hidden='false'. Returns False when the post doesn't exist."""
    conn = get_connection()
    try:
        exists = conn.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone()
        if exists is None:
            return False
        conn.execute(
            "UPDATE posts SET hidden = 'false' WHERE id = ? AND hidden = 'true'",
            (post_id,),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def hide_posts_by_urls(urls):
    """Bulk-hide posts by URL (dead-link results), idempotent. Returns count."""
    if not urls:
        return 0
    conn = get_connection()
    try:
        cursor = conn.executemany(
            "UPDATE posts SET hidden = 'true' WHERE url = ? AND hidden = 'false'",
            [(u,) for u in urls],
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


# ── Bookmarks ──────────────────────────────────────────

def add_bookmark(post_id):
    conn = get_connection()
    try:
        exists = conn.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone()
        if exists is None:
            return False
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


def get_bookmark_count():
    conn = get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
    finally:
        conn.close()


def get_bookmarks(limit=50, offset=0, sort="oldest", subreddit=None, start_date=None, end_date=None, q=None, min_score=None, min_comments=None):
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
            where += " AND p.created_utc < ?"
            params.append(end_date)
        clauses, clause_params = _min_filter_clauses("p.", min_score, min_comments)
        for c in clauses:
            where += c
        params.extend(clause_params)
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

        total = conn.execute("SELECT COUNT(*) " + where, params).fetchone()[0]
        rows = conn.execute(
            "SELECT p.*, b.created_at as bookmarked_at "
            f"{where} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(row) for row in rows], total
    finally:
        conn.close()


# ── Dashboard aggregations ─────────────────────────

# The dashboard re-reads whole-table aggregates that change only when the
# scraper inserts. A short TLR keeps the page fast during long scrapes
# without ever serving meaningfully stale numbers.
_DASHBOARD_CACHE_TTL = 60.0
_dashboard_cache = {"at": 0.0, "data": None}


def get_dashboard_data(top_posts_limit=10):
    """One pass over the aggregates the dashboard page needs.

    Per-subreddit counts/avg score, monthly post series and top posts. The
    whole-table scans are bounded by the existing indexes where possible;
    anything unindexed is a single sequential pass kept behind one connection
    so the dashboard costs a handful of round trips, not one per panel.
    Results are cached for a minute — the underlying numbers only move when
    the scraper inserts.
    """
    now = time.monotonic()
    cached = _dashboard_cache["data"]
    if cached is not None and now - _dashboard_cache["at"] < _DASHBOARD_CACHE_TTL:
        return cached

    conn = get_connection()
    try:
        totals = dict(
            conn.execute(
                "SELECT COUNT(*) AS posts, COUNT(DISTINCT subreddit) AS subreddits, "
                "MIN(created_utc) AS first_post, MAX(created_utc) AS last_post, "
                "SUM(score) AS total_score FROM posts WHERE hidden = 'false'"
            ).fetchone()
        )
        totals["bookmarks"] = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
        totals["hidden"] = conn.execute(
            "SELECT COUNT(*) FROM posts WHERE hidden = 'true'"
        ).fetchone()[0]

        per_subreddit = [
            dict(r)
            for r in conn.execute(
                "SELECT subreddit, COUNT(*) AS count, AVG(score) AS avg_score, "
                "MAX(created_utc) AS last_post FROM posts WHERE hidden = 'false' "
                "GROUP BY subreddit ORDER BY count DESC"
            ).fetchall()
        ]

        monthly = [
            dict(r)
            for r in conn.execute(
                "SELECT strftime('%Y-%m', created_utc, 'unixepoch') AS month, COUNT(*) AS count "
                "FROM posts WHERE hidden = 'false' GROUP BY month ORDER BY month"
            ).fetchall()
        ]

        top_posts = [
            dict(r)
            for r in conn.execute(
                "SELECT id, title, url, subreddit, score, num_comments, created_utc "
                "FROM posts WHERE hidden = 'false' ORDER BY score DESC LIMIT ?",
                (top_posts_limit,),
            ).fetchall()
        ]

        data = {
            "totals": totals,
            "per_subreddit": per_subreddit,
            "monthly": monthly,
            "top_posts": top_posts,
        }
    finally:
        conn.close()

    _dashboard_cache["at"] = now
    _dashboard_cache["data"] = data
    return data
