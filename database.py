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
        """
    )
    conn.commit()
    conn.close()


def insert_post(post):
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO posts (id, url, title, score, num_comments, created_utc, subreddit) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            post["id"],
            post["url"],
            post["title"],
            post["score"],
            post["num_comments"],
            post["created_utc"],
            post["subreddit"],
        ),
    )
    conn.commit()
    conn.close()


def insert_posts(posts):
    conn = get_connection()
    for post in posts:
        conn.execute(
            "INSERT OR IGNORE INTO posts (id, url, title, score, num_comments, created_utc, subreddit) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                post["id"],
                post["url"],
                post["title"],
                post["score"],
                post["num_comments"],
                post["created_utc"],
                post["subreddit"],
            ),
        )
    conn.commit()
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


def get_posts(subreddit=None, start_date=None, end_date=None, sort="score_desc", limit=100, q=None):
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
    query += " LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
