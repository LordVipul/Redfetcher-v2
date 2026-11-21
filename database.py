import logging
import math
import os
import sqlite3
import threading
import time

import config

log = logging.getLogger("redfetcher")

# get_posts() uses one indexed branch per subreddit up to this (limit + offset)
# window; past it the IN-list fallback keeps fan-out memory bounded.
GROUP_BRANCH_MAX_WINDOW = 5000

_COUNT_CACHE_TTL = 5.0
_COUNT_CACHE_MAX = 256
_count_cache = {}
_count_cache_lock = threading.Lock()


def _cached_count(conn, key, sql, params):
    now = time.monotonic()
    hit = _count_cache.get(key)
    if hit is not None and now - hit[0] < _COUNT_CACHE_TTL:
        return hit[1]
    with _count_cache_lock:
        now = time.monotonic()
        hit = _count_cache.get(key)
        if hit is not None and now - hit[0] < _COUNT_CACHE_TTL:
            return hit[1]
        count = conn.execute(sql, params).fetchone()[0]
        if len(_count_cache) >= _COUNT_CACHE_MAX:
            _count_cache.pop(next(iter(_count_cache)))
        _count_cache[key] = (now, count)
        return count


def get_connection():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-262144")
    conn.execute("PRAGMA mmap_size=2147483648")
    conn.execute("PRAGMA busy_timeout = 5000")
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
    conn.execute("CREATE INDEX idx_bookmarks_created ON bookmarks(created_at)")


def drop_redundant_indexes(conn):
    """Remove indexes superseded by composite prefixes or a UNIQUE constraint."""
    for name in (
        "idx_posts_subreddit",
        "idx_posts_subreddit_score_cmt",
        "idx_posts_score_cmt",
        "idx_bookmarks_post_id",
    ):
        conn.execute(f"DROP INDEX IF EXISTS {name}")


# Bump when the summary trigger definitions change. Trigger-only changes do
# not alter the aggregates, so a bump replaces the triggers without a rebuild;
# the data itself is only rebuilt when the consistency check below fails.
_SUMMARY_VERSION = 2

_SUMMARY_TRIGGER_NAMES = (
    "stats_posts_created_required",
    "stats_posts_ai",
    "stats_posts_au",
    "stats_posts_ad",
    "stats_posts_score",
)

_SUMMARY_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_posts_subreddit_hidden ON posts(subreddit, hidden);

CREATE TABLE IF NOT EXISTS stats_totals (
    k INTEGER PRIMARY KEY CHECK (k = 1),
    posts INTEGER NOT NULL DEFAULT 0,
    sum_score INTEGER NOT NULL DEFAULT 0,
    first_post REAL,
    last_post REAL,
    hidden INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stats_subreddit (
    subreddit TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0,
    sum_score INTEGER NOT NULL DEFAULT 0,
    last_post REAL
);

CREATE TABLE IF NOT EXISTS stats_month (
    month TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stats_meta (
    k TEXT PRIMARY KEY,
    v INTEGER NOT NULL
);
"""

_SUMMARY_TRIGGERS = (
    """
    CREATE TRIGGER stats_posts_created_required BEFORE INSERT ON posts
    WHEN NEW.created_utc IS NULL
    BEGIN
        SELECT RAISE(IGNORE);
    END
    """,
    """
    CREATE TRIGGER stats_posts_ai AFTER INSERT ON posts
    BEGIN
        UPDATE stats_totals SET
            posts = posts + CASE WHEN NEW.hidden = 'false' THEN 1 ELSE 0 END,
            hidden = hidden + CASE WHEN NEW.hidden = 'true' THEN 1 ELSE 0 END,
            sum_score = sum_score + CASE WHEN NEW.hidden = 'false' THEN COALESCE(NEW.score, 0) ELSE 0 END,
            first_post = CASE WHEN NEW.hidden = 'false' AND (first_post IS NULL OR NEW.created_utc < first_post) THEN NEW.created_utc ELSE first_post END,
            last_post = CASE WHEN NEW.hidden = 'false' AND (last_post IS NULL OR NEW.created_utc > last_post) THEN NEW.created_utc ELSE last_post END
        WHERE k = 1;

        INSERT INTO stats_subreddit(subreddit, count, sum_score, last_post)
        SELECT NEW.subreddit, 1, COALESCE(NEW.score, 0), NEW.created_utc
        WHERE NEW.hidden = 'false'
        ON CONFLICT(subreddit) DO UPDATE SET
            count = count + 1,
            sum_score = sum_score + COALESCE(NEW.score, 0),
            last_post = MAX(COALESCE(last_post, NEW.created_utc), NEW.created_utc);

        INSERT INTO stats_month(month, count)
        SELECT strftime('%Y-%m', NEW.created_utc, 'unixepoch'), 1
        WHERE NEW.hidden = 'false'
        ON CONFLICT(month) DO UPDATE SET count = count + 1;
    END
    """,
    """
    CREATE TRIGGER stats_posts_au AFTER UPDATE OF hidden ON posts
    WHEN OLD.hidden IS NOT NEW.hidden
    BEGIN
        UPDATE stats_totals SET
            posts = posts + CASE WHEN NEW.hidden = 'false' THEN 1 ELSE -1 END,
            hidden = hidden + CASE WHEN NEW.hidden = 'true' THEN 1 ELSE -1 END,
            sum_score = sum_score + CASE WHEN NEW.hidden = 'false' THEN COALESCE(NEW.score, 0) ELSE -COALESCE(NEW.score, 0) END,
            first_post = CASE
                WHEN NEW.hidden = 'false' THEN MIN(COALESCE(first_post, NEW.created_utc), NEW.created_utc)
                WHEN OLD.created_utc = first_post THEN (SELECT MIN(created_utc) FROM posts WHERE hidden = 'false')
                ELSE first_post END,
            last_post = CASE
                WHEN NEW.hidden = 'false' THEN MAX(COALESCE(last_post, NEW.created_utc), NEW.created_utc)
                WHEN OLD.created_utc = last_post THEN (SELECT MAX(created_utc) FROM posts WHERE hidden = 'false')
                ELSE last_post END
        WHERE k = 1;

        INSERT INTO stats_subreddit(subreddit, count, sum_score, last_post)
        VALUES (
            NEW.subreddit,
            CASE WHEN NEW.hidden = 'false' THEN 1 ELSE -1 END,
            CASE WHEN NEW.hidden = 'false' THEN COALESCE(NEW.score, 0) ELSE -COALESCE(NEW.score, 0) END,
            NEW.created_utc
        )
        ON CONFLICT(subreddit) DO UPDATE SET
            count = count + CASE WHEN NEW.hidden = 'false' THEN 1 ELSE -1 END,
            sum_score = sum_score + CASE WHEN NEW.hidden = 'false' THEN COALESCE(NEW.score, 0) ELSE -COALESCE(NEW.score, 0) END,
            last_post = (SELECT MAX(created_utc) FROM posts WHERE subreddit = NEW.subreddit AND hidden = 'false');

        INSERT INTO stats_month(month, count)
        VALUES (
            strftime('%Y-%m', NEW.created_utc, 'unixepoch'),
            CASE WHEN NEW.hidden = 'false' THEN 1 ELSE -1 END
        )
        ON CONFLICT(month) DO UPDATE SET
            count = count + CASE WHEN NEW.hidden = 'false' THEN 1 ELSE -1 END;

        DELETE FROM stats_subreddit WHERE subreddit = NEW.subreddit AND count <= 0;
        DELETE FROM stats_month WHERE month = strftime('%Y-%m', NEW.created_utc, 'unixepoch') AND count <= 0;
    END
    """,
    """
    CREATE TRIGGER stats_posts_ad AFTER DELETE ON posts
    BEGIN
        UPDATE stats_totals SET
            posts = posts - CASE WHEN OLD.hidden = 'false' THEN 1 ELSE 0 END,
            hidden = hidden - CASE WHEN OLD.hidden = 'true' THEN 1 ELSE 0 END,
            sum_score = sum_score - CASE WHEN OLD.hidden = 'false' THEN COALESCE(OLD.score, 0) ELSE 0 END,
            first_post = CASE WHEN OLD.hidden = 'false' AND OLD.created_utc = first_post
                THEN (SELECT MIN(created_utc) FROM posts WHERE hidden = 'false') ELSE first_post END,
            last_post = CASE WHEN OLD.hidden = 'false' AND OLD.created_utc = last_post
                THEN (SELECT MAX(created_utc) FROM posts WHERE hidden = 'false') ELSE last_post END
        WHERE k = 1;

        UPDATE stats_subreddit SET
            count = count - CASE WHEN OLD.hidden = 'false' THEN 1 ELSE 0 END,
            sum_score = sum_score - CASE WHEN OLD.hidden = 'false' THEN COALESCE(OLD.score, 0) ELSE 0 END,
            last_post = (SELECT MAX(created_utc) FROM posts WHERE subreddit = OLD.subreddit AND hidden = 'false')
        WHERE subreddit = OLD.subreddit;

        UPDATE stats_month SET
            count = count - CASE WHEN OLD.hidden = 'false' THEN 1 ELSE 0 END
        WHERE month = strftime('%Y-%m', OLD.created_utc, 'unixepoch');

        DELETE FROM stats_subreddit WHERE subreddit = OLD.subreddit AND count <= 0;
        DELETE FROM stats_month WHERE month = strftime('%Y-%m', OLD.created_utc, 'unixepoch') AND count <= 0;
    END
    """,
    """
    CREATE TRIGGER stats_posts_score AFTER UPDATE OF score ON posts
    WHEN OLD.score IS NOT NEW.score
     AND OLD.hidden = 'false' AND NEW.hidden = 'false'
    BEGIN
        UPDATE stats_totals SET
            sum_score = sum_score + COALESCE(NEW.score, 0) - COALESCE(OLD.score, 0)
        WHERE k = 1
          AND EXISTS (SELECT 1 FROM stats_subreddit WHERE subreddit = NEW.subreddit);

        UPDATE stats_subreddit SET
            sum_score = sum_score + COALESCE(NEW.score, 0) - COALESCE(OLD.score, 0)
        WHERE subreddit = NEW.subreddit;
    END
    """,
)


def _summary_version(conn):
    row = conn.execute(
        "SELECT v FROM stats_meta WHERE k = 'schema_version'"
    ).fetchone()
    return row[0] if row else None


def _ensure_summary_triggers(conn):
    """Replace summary triggers when their version or set is stale.

    ``CREATE TRIGGER IF NOT EXISTS`` cannot update an existing trigger body, so
    the trigger set is versioned in ``stats_meta``. The drop and recreate are
    one transaction: other connections keep seeing the old set until commit.
    """
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'trigger' AND name LIKE 'stats_posts_%'"
        )
    }
    if (
        _summary_version(conn) == _SUMMARY_VERSION
        and set(_SUMMARY_TRIGGER_NAMES) <= existing
    ):
        return

    conn.execute("BEGIN")
    try:
        for name in existing:
            conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
        for statement in _SUMMARY_TRIGGERS:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO stats_meta(k, v) VALUES('schema_version', ?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (_SUMMARY_VERSION,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _summary_is_consistent(conn):
    row = conn.execute("SELECT posts FROM stats_totals WHERE k = 1").fetchone()
    if row is None:
        return False
    total = row[0]
    sub_total = conn.execute(
        "SELECT COALESCE(SUM(count), 0) FROM stats_subreddit"
    ).fetchone()[0]
    month_total = conn.execute(
        "SELECT COALESCE(SUM(count), 0) FROM stats_month"
    ).fetchone()[0]
    return sub_total == total and month_total == total


def _backfill_summary(conn):
    """Seed the summary tables, rebuilding when they are absent or inconsistent."""
    if _summary_is_consistent(conn):
        return
    if conn.execute("SELECT 1 FROM stats_totals WHERE k = 1").fetchone() is None:
        log.info("Seeding summary tables from posts")
    else:
        log.warning("Summary tables inconsistent; rebuilding from posts")
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM stats_subreddit")
        conn.execute("DELETE FROM stats_month")
        conn.execute("DELETE FROM stats_totals")
        conn.execute(
            "INSERT INTO stats_totals(k, posts, sum_score, first_post, last_post, hidden) "
            "VALUES (1, 0, 0, NULL, NULL, 0)"
        )
        conn.execute(
            "UPDATE stats_totals SET "
            "posts = (SELECT COUNT(*) FROM posts WHERE hidden = 'false'), "
            "sum_score = (SELECT COALESCE(SUM(score), 0) FROM posts WHERE hidden = 'false'), "
            "first_post = (SELECT MIN(created_utc) FROM posts WHERE hidden = 'false'), "
            "last_post = (SELECT MAX(created_utc) FROM posts WHERE hidden = 'false'), "
            "hidden = (SELECT COUNT(*) FROM posts WHERE hidden = 'true') "
            "WHERE k = 1"
        )
        conn.execute(
            "INSERT INTO stats_subreddit(subreddit, count, sum_score, last_post) "
            "SELECT subreddit, COUNT(*), COALESCE(SUM(score), 0), MAX(created_utc) "
            "FROM posts WHERE hidden = 'false' GROUP BY subreddit"
        )
        conn.execute(
            "INSERT INTO stats_month(month, count) "
            "SELECT strftime('%Y-%m', created_utc, 'unixepoch'), COUNT(*) "
            "FROM posts WHERE hidden = 'false' GROUP BY 1"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


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
                hidden TEXT NOT NULL DEFAULT 'false',
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_utc);
            CREATE INDEX IF NOT EXISTS idx_posts_score ON posts(score);
            CREATE INDEX IF NOT EXISTS idx_posts_num_comments ON posts(num_comments);
            CREATE INDEX IF NOT EXISTS idx_posts_subreddit_created ON posts(subreddit, created_utc);
            CREATE INDEX IF NOT EXISTS idx_posts_subreddit_score ON posts(subreddit, score);
            CREATE INDEX IF NOT EXISTS idx_posts_subreddit_comments ON posts(subreddit, num_comments);
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
            );
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
        had_hidden = conn.execute(
            "SELECT 1 FROM pragma_table_info('posts') WHERE name='hidden'"
        ).fetchone() is not None
        migrate_bookmarks_foreign_key(conn)
        drop_redundant_indexes(conn)
        if not had_hidden:
            conn.execute("ALTER TABLE posts ADD COLUMN hidden TEXT NOT NULL DEFAULT 'false'")
        _ensure_fts_update_guard(conn)
        if not had_fts:
            conn.execute("INSERT INTO posts_fts(posts_fts) VALUES('rebuild')")
        conn.executescript(_SUMMARY_SCHEMA)
        _ensure_summary_triggers(conn)
        _backfill_summary(conn)
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


def _valid_post_row(post, index):
    """Return a DB row tuple for a valid post, or None after logging why not."""
    if not isinstance(post, dict):
        log.warning("Skipping malformed post at batch index %d: %r", index, post)
        return None
    post_id = post.get("id")
    url = post.get("url")
    subreddit = post.get("subreddit")
    if not isinstance(post_id, str) or not post_id.strip():
        log.warning("Skipping post at batch index %d: invalid id %r", index, post_id)
        return None
    if not isinstance(url, str) or not url.strip():
        log.warning("Skipping post %r: invalid url %r", post_id, url)
        return None
    if not isinstance(subreddit, str) or not subreddit.strip():
        log.warning("Skipping post %r: invalid subreddit %r", post_id, subreddit)
        return None
    try:
        created_utc = float(post.get("created_utc"))
    except (TypeError, ValueError):
        created_utc = None
    if created_utc is None or not math.isfinite(created_utc):
        log.warning(
            "Skipping post %r: invalid created_utc %r",
            post_id,
            post.get("created_utc"),
        )
        return None
    return (
        post_id.strip(),
        url.strip(),
        post.get("title"),
        post.get("score"),
        post.get("num_comments"),
        created_utc,
        subreddit.strip(),
    )


def insert_posts(posts):
    """Insert valid posts, skipping malformed ones; return the number inserted."""
    if not posts:
        return 0
    rows = []
    for index, post in enumerate(posts):
        row = _valid_post_row(post, index)
        if row is not None:
            rows.append(row)
    if not rows:
        return 0
    conn = get_connection()
    try:
        cursor = conn.executemany(
            "INSERT OR IGNORE INTO posts (id, url, title, score, num_comments, created_utc, subreddit) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
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
        order_by = f"{col} {direction}, rowid {direction}"
        # `offset` overrides the page-derived offset (single-row top-ups after
        # hides, which must read past the page window without shifting pages).
        if offset is None:
            offset = (page - 1) * limit

        simple_count = not (
            start_date or end_date
            or min_score is not None or min_comments is not None
            or q
        )
        if simple_count:
            if subreddit:
                row = conn.execute(
                    "SELECT count FROM stats_subreddit WHERE subreddit = ?", (subreddit,)
                ).fetchone()
                total = row["count"] if row else 0
            elif subreddits:
                placeholders = ",".join("?" * len(subreddits))
                total = conn.execute(
                    f"SELECT COALESCE(SUM(count), 0) FROM stats_subreddit WHERE subreddit IN ({placeholders})",
                    list(subreddits),
                ).fetchone()[0]
            else:
                total = conn.execute("SELECT posts FROM stats_totals WHERE k = 1").fetchone()[0]
        else:
            if subreddit:
                count_sql = f"SELECT COUNT(*) FROM posts WHERE 1=1 AND subreddit = ?{filters}"
                count_params = [subreddit] + params
            elif subreddits:
                placeholders = ",".join("?" * len(subreddits))
                count_sql = f"SELECT COUNT(*) FROM posts WHERE 1=1 AND subreddit IN ({placeholders}){filters}"
                count_params = list(subreddits) + params
            else:
                count_sql = f"SELECT COUNT(*) FROM posts WHERE 1=1{filters}"
                count_params = params
            total = _cached_count(
                conn,
                (subreddit, tuple(subreddits) if subreddits else None, tuple(params)),
                count_sql,
                count_params,
            )

        # Group queries: one indexed per-subreddit branch, merged and sorted in
        # SQL. A single IN-list makes SQLite temp-b-tree-sort every matching row
        # (0.8s on the AI group); branches stay on the (subreddit, <sort col>)
        # index (~5ms). Branches return only (rowid, sort column) so the outer
        # sort stays narrow; full rows are fetched after the final page slice.
        # Past GROUP_BRANCH_MAX_WINDOW the IN-list fallback keeps fan-out
        # bounded. rowid breaks ties so pages stay stable.
        if subreddits and len(subreddits) > 1 and limit + offset <= GROUP_BRANCH_MAX_WINDOW:
            branches, branch_params = [], []
            for sub in subreddits:
                branches.append(
                    f"SELECT * FROM (SELECT rowid AS _rowid, {col} FROM posts "
                    f"WHERE 1=1 AND subreddit = ?{filters} "
                    f"ORDER BY {order_by} LIMIT ?)"
                )
                branch_params.extend([sub] + params + [limit + offset])
            sql = (
                " UNION ALL ".join(branches)
                + f" ORDER BY {col} {direction}, _rowid {direction} LIMIT ? OFFSET ?"
            )
            keys = conn.execute(sql, branch_params + [limit, offset]).fetchall()
            rowids = [key["_rowid"] for key in keys]
            if not rowids:
                return [], total
            placeholders = ",".join("?" * len(rowids))
            by_rowid = {
                row["_rowid"]: {k: v for k, v in dict(row).items() if k != "_rowid"}
                for row in conn.execute(
                    f"SELECT *, rowid AS _rowid FROM posts WHERE rowid IN ({placeholders})",
                    rowids,
                )
            }
            return [by_rowid[rowid] for rowid in rowids if rowid in by_rowid], total

        if subreddits:
            placeholders = ",".join("?" * len(subreddits))
            rows = conn.execute(
                f"SELECT * FROM posts WHERE 1=1 AND subreddit IN ({placeholders}){filters} "
                f"ORDER BY {order_by} LIMIT ? OFFSET ?",
                list(subreddits) + params + [limit, offset],
            ).fetchall()
        elif subreddit:
            rows = conn.execute(
                f"SELECT * FROM posts WHERE 1=1 AND subreddit = ?{filters} "
                f"ORDER BY {order_by} LIMIT ? OFFSET ?",
                [subreddit] + params + [limit, offset],
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM posts WHERE 1=1{filters} "
                f"ORDER BY {order_by} LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
        return [dict(row) for row in rows], total
    finally:
        conn.close()


# ── Hide / curation ───────────────────────────────

def hide_post(post_id):
    """Hide one post unless bookmarked. Returns 'missing', 'bookmarked' or 'hidden'."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM posts WHERE id = ?) AS exists_, "
            "EXISTS(SELECT 1 FROM bookmarks WHERE post_id = ?) AS bookmarked",
            (post_id, post_id),
        ).fetchone()
        if not row["exists_"]:
            return "missing"
        if row["bookmarked"]:
            return "bookmarked"
        # Idempotent: already-hidden rows are left alone (no churn).
        conn.execute(
            "UPDATE posts SET hidden = 'true' WHERE id = ? AND hidden = 'false'",
            (post_id,),
        )
        conn.commit()
        return "hidden"
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
            "UPDATE posts SET hidden = 'true' WHERE url = ? AND hidden = 'false' "
            "AND NOT EXISTS (SELECT 1 FROM bookmarks WHERE post_id = posts.id)",
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
        visible = conn.execute(
            "SELECT 1 FROM posts WHERE id = ? AND hidden = 'false'", (post_id,)
        ).fetchone()
        if visible is None:
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


_VISIBLE_BOOKMARK_COUNT_SQL = (
    "SELECT COUNT(*) FROM bookmarks b CROSS JOIN posts p ON p.id = b.post_id "
    "WHERE p.hidden = 'false'"
)


_SIDEBAR_CACHE_TTL = 60.0
_SUBREDDIT_CACHE_TTL = 300.0
_sidebar_cache = {"at": 0.0, "data": None}
_subreddit_cache = {"at": 0.0, "data": None}
_sidebar_cache_lock = threading.Lock()


def _seed_subreddits(stats):
    data = sorted(s["subreddit"] for s in stats)
    _subreddit_cache["at"] = time.monotonic()
    _subreddit_cache["data"] = data
    return data


def get_subreddits():
    """Return visible subreddit names, cached for a few minutes."""
    now = time.monotonic()
    cached = _subreddit_cache["data"]
    if cached is not None and now - _subreddit_cache["at"] < _SUBREDDIT_CACHE_TTL:
        return cached

    with _sidebar_cache_lock:
        now = time.monotonic()
        cached = _subreddit_cache["data"]
        if cached is not None and now - _subreddit_cache["at"] < _SUBREDDIT_CACHE_TTL:
            return cached

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT subreddit FROM stats_subreddit WHERE count > 0 ORDER BY subreddit"
            ).fetchall()
        finally:
            conn.close()

        data = [r["subreddit"] for r in rows]
        _subreddit_cache["at"] = time.monotonic()
        _subreddit_cache["data"] = data
        return data


def get_sidebar_data():
    """Return sidebar stats, bookmark count, and subreddit names as one bundle.

    Counts come from the maintained summary tables, so this is cheap even with
    millions of posts. The short cache still smooths out rapid repeated reads.
    """
    now = time.monotonic()
    cached = _sidebar_cache["data"]
    if cached is not None and now - _sidebar_cache["at"] < _SIDEBAR_CACHE_TTL:
        return cached

    with _sidebar_cache_lock:
        now = time.monotonic()
        cached = _sidebar_cache["data"]
        if cached is not None and now - _sidebar_cache["at"] < _SIDEBAR_CACHE_TTL:
            return cached

        conn = get_connection()
        try:
            stats = [
                dict(row)
                for row in conn.execute(
                    "SELECT subreddit, count, last_post AS last_updated "
                    "FROM stats_subreddit WHERE count > 0 ORDER BY count DESC"
                ).fetchall()
            ]
            bookmark_count = conn.execute(_VISIBLE_BOOKMARK_COUNT_SQL).fetchone()[0]
        finally:
            conn.close()

        data = {
            "stats": stats,
            "bookmark_count": bookmark_count,
            "subreddits": _seed_subreddits(stats),
        }
        _sidebar_cache["at"] = time.monotonic()
        _sidebar_cache["data"] = data
        return data


def get_bookmarks(limit=50, offset=0, sort="oldest", subreddit=None, start_date=None, end_date=None, q=None, min_score=None, min_comments=None):
    conn = get_connection()
    try:
        where = """
            FROM bookmarks b
            CROSS JOIN posts p ON p.id = b.post_id
            WHERE p.hidden = 'false'
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
            "oldest": "b.created_at ASC, b.id ASC",
            "newest": "b.created_at DESC, b.id DESC",
            "score_desc": "p.score DESC, b.id DESC",
            "score_asc": "p.score ASC, b.id ASC",
            "comments_desc": "p.num_comments DESC, b.id DESC",
            "comments_asc": "p.num_comments ASC, b.id ASC",
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

# The dashboard reads maintained summary tables, so it stays fast no matter
# how large posts grows. A short TTL smooths out rapid repeated reads.
_DASHBOARD_CACHE_TTL = 60.0
_dashboard_cache = {"at": 0.0, "data": None}


def get_dashboard_data(top_posts_limit=10):
    """Return the dashboard's aggregates from the maintained summary tables.

    Totals, per-subreddit counts/avg score and the monthly series are O(1) to
    O(subreddits) now; only the top-posts list still reads posts, via the
    indexed score sort. Results are cached briefly for repeated reads.
    """
    now = time.monotonic()
    cached = _dashboard_cache["data"]
    if cached is not None and now - _dashboard_cache["at"] < _DASHBOARD_CACHE_TTL:
        return cached

    conn = get_connection()
    try:
        totals = dict(
            conn.execute(
                "SELECT posts, sum_score AS total_score, first_post, last_post, hidden "
                "FROM stats_totals WHERE k = 1"
            ).fetchone()
        )
        totals["subreddits"] = conn.execute(
            "SELECT COUNT(*) FROM stats_subreddit WHERE count > 0"
        ).fetchone()[0]
        totals["bookmarks"] = conn.execute(_VISIBLE_BOOKMARK_COUNT_SQL).fetchone()[0]

        per_subreddit = [
            dict(r)
            for r in conn.execute(
                "SELECT subreddit, count, "
                "CASE WHEN count > 0 THEN CAST(sum_score AS REAL) / count ELSE 0 END AS avg_score, "
                "last_post FROM stats_subreddit WHERE count > 0 ORDER BY count DESC"
            ).fetchall()
        ]

        monthly = [
            dict(r)
            for r in conn.execute(
                "SELECT month, count FROM stats_month ORDER BY month"
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

    _dashboard_cache["at"] = time.monotonic()
    _dashboard_cache["data"] = data
    return data
