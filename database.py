import sqlite3
import config


def get_connection():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
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
        """
    )
    conn.commit()
    conn.close()


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
    row = conn.execute(
        "SELECT MAX(created_utc) FROM posts WHERE subreddit = ?", (subreddit,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def get_stats():
    conn = get_connection()
    rows = conn.execute(
        "SELECT subreddit, COUNT(*) as count, MAX(created_utc) as last_updated FROM posts GROUP BY subreddit ORDER BY count DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_posts(subreddit=None, start_date=None, end_date=None, sort="score_desc", limit=100, q=None, page=1):
    conn = get_connection()
    query = "SELECT * FROM posts WHERE 1=1"
    params = []

    if subreddit:
        query += " AND subreddit = ?"
        params.append(subreddit)
    if start_date:
        query += " AND created_utc >= ?"
        params.append(start_date)
    if end_date:
        query += " AND created_utc <= ?"
        params.append(end_date)
    if q:
        query += " AND title LIKE ?"
        params.append(f"%{q}%")

    sort_map = {
        "score_desc": "score DESC",
        "score_asc": "score ASC",
        "comments_desc": "num_comments DESC",
        "comments_asc": "num_comments ASC",
        "newest": "created_utc DESC",
        "oldest": "created_utc ASC",
    }
    query += f" ORDER BY {sort_map.get(sort, 'score DESC')}"
    offset = (page - 1) * limit
    query += " LIMIT ? OFFSET ?"
    params.append(limit)
    params.append(offset)

    rows = conn.execute(query, params).fetchall()

    # Get total count for pagination
    count_query = "SELECT COUNT(*) FROM posts WHERE 1=1"
    count_params = []
    if subreddit:
        count_query += " AND subreddit = ?"
        count_params.append(subreddit)
    if start_date:
        count_query += " AND created_utc >= ?"
        count_params.append(start_date)
    if end_date:
        count_query += " AND created_utc <= ?"
        count_params.append(end_date)
    if q:
        count_query += " AND title LIKE ?"
        count_params.append(f"%{q}%")
    total = conn.execute(count_query, count_params).fetchone()[0]

    conn.close()
    return [dict(r) for r in rows], total


def get_total_count(subreddit=None):
    conn = get_connection()
    if subreddit:
        row = conn.execute("SELECT COUNT(*) FROM posts WHERE subreddit = ?", (subreddit,)).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM posts").fetchone()
    conn.close()
    return row[0] if row else 0


def get_all_subreddits():
    conn = get_connection()
    rows = conn.execute("SELECT DISTINCT subreddit FROM posts ORDER BY subreddit").fetchall()
    conn.close()
    return [r["subreddit"] for r in rows]


# ── Bookmarks ──────────────────────────────────────────────────

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
    row = conn.execute("SELECT 1 FROM bookmarks WHERE post_id = ?", (post_id,)).fetchone()
    conn.close()
    return row is not None


def get_bookmark_ids():
    conn = get_connection()
    rows = conn.execute("SELECT post_id FROM bookmarks ORDER BY created_at ASC").fetchall()
    conn.close()
    return [r["post_id"] for r in rows]


def get_bookmarks(limit=50, offset=0, sort="oldest", subreddit=None):
    conn = get_connection()
    query = """
        SELECT p.*, b.created_at as bookmarked_at
        FROM posts p
        INNER JOIN bookmarks b ON p.id = b.post_id
        WHERE 1=1
    """
    params = []

    if subreddit:
        query += " AND p.subreddit = ?"
        params.append(subreddit)

    sort_map = {
        "oldest": "b.created_at ASC",
        "newest": "b.created_at DESC",
        "score_desc": "p.score DESC",
        "score_asc": "p.score ASC",
        "comments_desc": "p.num_comments DESC",
        "comments_asc": "p.num_comments ASC",
    }
    query += f" ORDER BY {sort_map.get(sort, 'b.created_at ASC')}"
    query += " LIMIT ? OFFSET ?"
    params.append(limit)
    params.append(offset)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_bookmarks_count(subreddit=None):
    conn = get_connection()
    query = "SELECT COUNT(*) FROM bookmarks b INNER JOIN posts p ON p.id = b.post_id WHERE 1=1"
    params = []
    if subreddit:
        query += " AND p.subreddit = ?"
        params.append(subreddit)
    total = conn.execute(query, params).fetchone()[0]
    conn.close()
    return total
