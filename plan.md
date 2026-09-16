# RedFetcher v2 — Reddit Post Archiver

## Project Overview

A local web application with a Python scraper that archives **all posts from any subreddit since its creation** using the Arctic Shift API. Posts are stored in SQLite with links, upvote/downvote counts, and comment counts. The web UI supports advanced filtering and sorting. The scraper is resumable — interruption does not cause data loss.

---

## Research & Source Material

### Why the Previous Project (RedFetcher) Broke

The original RedFetcher (`D:\Code\_archive\RedFetcher`) was a client-side-only web app hitting `https://www.reddit.com/r/{sub}/top/.json?t=day` directly with `fetch()`. Reddit began returning **HTTP 403 Forbidden from datacenter IPs** in May 2026, making all direct `.json` fetches from a server impossible without authentication or a proxy. The project's README documents known issues: rate-limit caching bugs, calendar-day filter mismatches, and corrupt `localStorage` crashes.

### Data Source: Arctic Shift API

- **Base URL**: `https://arctic-shift.photon-reddit.com/api`
- **Authentication**: None required — free, community-maintained
- **Maintainer**: Arthur Heitmann, `github.com/ArthurHeitmann/arctic_shift`
- **Coverage**: Full Reddit archive back to January 2005 (~2.5 billion items)
- **Status**: No uptime SLA, community-maintained
- **Alternatives considered and rejected**:
  - Pushshift — public API revoked May 2023, dead
  - PRAW — requires Reddit developer account + OAuth, listing cap ~1,000 posts
  - Reddit `.json` API — returns 403 from datacenter IPs since May 2026
  - Apify actors — paid service, not local

### Arctic Shift API Details

| Property | Value |
|----------|-------|
| **Search endpoint** | `GET /api/posts/search` |
| **Comment endpoint** | `GET /api/comments/search` |
| **Max posts per request** | 100 (1000 returns HTTP 400) |
| **Sort options** | `asc` (ascending), `desc` (descending) |
| **`asc` speed** | ~0.13s per request |
| **`desc` speed** | ~1.5s per request |
| **Pagination** | `after` / `before` params accept dates (YYYY-MM-DD) |
| **Date filtering** | `after` and `before` parameters filter posts by date |
| **Response format** | `{"data": [post_dict, ...]}` — flat list |
| **Rate limit headers** | `X-RateLimit-Remaining`, `X-RateLimit-Reset` |
| **Observed rate limit** | ~2,000 requests per 31-second window |
| **Stalls** | Timeouts on dense time windows every ~7,000 posts |
| **`after=0` bug** | Returns HTTP 400 — omit entirely on first page |
| **Keyword search limitation** | `title`, `selftext`, `query` params only work with `author` or `subreddit` |

### Performance Benchmarks (verified via live testing)

- 10 requests after cache warmup: **6.23 req/s** (~623 posts/s)
- Individual request: ~0.1–1.5s depending on Cloudflare cache state
- **Recommended safe rate**: ~2.5–3 req/s (`REQUEST_DELAY = 0.35s`)
- `arcshiftwrap` library handles retry/backoff internally

### Python Client: `arcshiftwrap` (v0.4.1)

```bash
pip install arcshiftwrap
```

Import path (critical — not from top-level):
```python
from arcshiftwrap.arctic_shift import collect_posts_by_windows, ArcticShiftClient
```

Key function signature:
```python
collect_posts_by_windows(
    client, 
    subreddit, 
    start: datetime,  # NOT string — must be datetime object
    end: datetime,    # NOT string
    step_hours=24, 
    limit=100, 
    fields=[...]
) → generator yielding post dicts
```

Gotchas confirmed via live testing:
- `start` and `end` must be **`datetime` objects**, not strings
- Max `limit` is 100 (not 1000)
- `sort=asc` is ~10x faster than `sort=desc`
- Response is `{"data": [...]}` — flat list of post dicts
- `after=0` returns HTTP 400
- Import must be from `arcshiftwrap.arctic_shift`, not `arcshiftwrap`

---

## Database Schema

```sql
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
```

- `id` is Reddit's base36 post ID (e.g., `t3_abc123` → `abc123`)
- `url` is the Reddit permalink (e.g., `/r/python/comments/abc123/...`)
- `score` is the upvote count
- `num_comments` is the comment count
- `created_utc` is Unix timestamp for date filtering
- `UNIQUE` constraint on `url` enables `INSERT OR IGNORE` dedup

---

## Project Structure

```
redfetcher-v2/
├── config.py          # Settings: subreddit list, API config, request delay
├── database.py        # SQLite init, insert/query helpers
├── scraper.py         # Arctic Shift scraping logic
├── server.py          # FastAPI app — routes, templates, app entry point
├── templates/
│   ├── index.html     # Landing page: subreddit list, stats, scrape triggers
│   └── posts.html     # Post listing with filters/sort
├── requirements.txt
├── .gitignore
└── plan.md            # This file
```

---

## Configuration (`config.py`)

```python
# List of subreddits to track
SUBREDDITS = [
    "programming",
    "python", 
    "unixporn",
    "linux",
]

# Arctic Shift API settings
API_BASE = "https://arctic-shift.photon-reddit.com/api"
REQUEST_DELAY = 0.35  # seconds between batches (~2.8 req/s, safe)
CLIENT_TIMEOUT = 60
MAX_RETRIES = 4
BACKOFF_FACTOR = 2.0

# Database
DB_PATH = "reddit_archive.db"

# Scraper settings
START_YEAR = 2005  # Start fetching from this year
POSTS_PER_REQUEST = 100  # Max allowed by API
SORT_ORDER = "asc"  # Faster than desc, chronological order
```

---

## Scraper Module (`scraper.py`)

### Resume Logic

```
First run:
  MAX(created_utc) returns NULL → start = datetime(2005, 1, 1)
  collect_posts_by_windows(start=2005-01-01, end=today)
  → Fetches ALL posts from inception to present

Subsequent runs after interruption:
  MAX(created_utc) returns e.g., 2025-06-15T12:00:00Z
  → start = that datetime
  collect_posts_by_windows(start=that_date, end=today)
  → Only fetches posts after the last stored post

Dedup:
  INSERT OR IGNORE on `url` (UNIQUE constraint)
  Already-stored posts are silently skipped
```

### Implementation

```python
from datetime import datetime
import time
from arcshiftwrap.arctic_shift import collect_posts_by_windows, ArcticShiftClient
import config
import database

def get_resume_point(subreddit: str) -> datetime:
    """Return the most recent created_utc for this subreddit,
    or datetime(2005, 1, 1) if none exists."""
    max_utc = database.get_max_created_utc(subreddit)
    if max_utc:
        return datetime.fromtimestamp(max_utc)
    return datetime(2005, 1, 1)

def scrape_subreddit(subreddit: str) -> int:
    """Fetch all posts from resume point to today.
    Returns the number of new posts inserted."""
    client = ArcticShiftClient(
        timeout=config.CLIENT_TIMEOUT,
        max_retries=config.MAX_RETRIES,
        backoff_factor=config.BACKOFF_FACTOR,
    )
    start = get_resume_point(subreddit)
    end = datetime.now()
    
    count = 0
    for post in collect_posts_by_windows(
        client,
        subreddit=subreddit,
        start=start,
        end=end,
        step_hours=24,
        limit=config.POSTS_PER_REQUEST,
        fields=["id", "title", "url", "score", "num_comments", "created_utc", "subreddit"]
    ):
        database.insert_post(post)
        count += 1
        time.sleep(config.REQUEST_DELAY)
    
    return count
```

### Multi-Subreddit Support

```python
def scrape_all():
    """Scrape all configured subreddits one at a time."""
    results = {}
    for subreddit in config.SUBREDDITS:
        count = scrape_subreddit(subreddit)
        results[subreddit] = count
    return results
```

---

## Database Module (`database.py`)

```python
import sqlite3
import config

def get_connection():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Create tables and indexes if they don't exist."""
    conn = get_connection()
    conn.executescript("""
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
    """)
    conn.commit()
    conn.close()

def insert_post(post: dict):
    """Insert a single post. INSERT OR IGNORE skips duplicates."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO posts (id, url, title, score, num_comments, created_utc, subreddit) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (post["id"], post["url"], post["title"], post["score"], post["num_comments"], post["created_utc"], post["subreddit"])
    )
    conn.commit()
    conn.close()

def get_max_created_utc(subreddit: str) -> float | None:
    """Return the most recent created_utc for a subreddit, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT MAX(created_utc) FROM posts WHERE subreddit = ?",
        (subreddit,)
    ).fetchone()
    conn.close()
    return row[0] if row else None

def get_stats() -> list:
    """Return post counts per subreddit."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT subreddit, COUNT(*) as count, MAX(created_utc) as last_updated FROM posts GROUP BY subreddit ORDER BY count DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_posts(subreddit=None, start_date=None, end_date=None, sort="score_desc", limit=100, q=None):
    """Query posts with optional filters."""
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
    
    # Sort
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
    """Return total post count, optionally filtered by subreddit."""
    conn = get_connection()
    if subreddit:
        row = conn.execute("SELECT COUNT(*) FROM posts WHERE subreddit = ?", (subreddit,)).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM posts").fetchone()
    conn.close()
    return row[0] if row else 0
```

---

## Web Server (`server.py`)

```python
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import config
import database
import scraper

app = FastAPI(title="RedFetcher v2")
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    stats = database.get_stats()
    return templates.TemplateResponse("index.html", {"request": request, "stats": stats, "subreddits": config.SUBREDDITS})

@app.post("/scrape/{subreddit}")
async def scrape(subreddit: str):
    count = scraper.scrape_subreddit(subreddit)
    return {"subreddit": subreddit, "new_posts": count}

@app.get("/posts", response_class=HTMLResponse)
async def posts(
    request: Request,
    subreddit: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    sort: str = Query("score_desc"),
    limit: int = Query(100),
    q: str = Query(None),
):
    results = database.get_posts(
        subreddit=subreddit, start_date=start, end_date=end,
        sort=sort, limit=limit, q=q
    )
    return templates.TemplateResponse("posts.html", {
        "request": request, "posts": results, "filters": {subreddit, start, end, sort, limit, q}
    })

@app.get("/api/posts")
async def api_posts(
    subreddit: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    sort: str = Query("score_desc"),
    limit: int = Query(100),
    q: str = Query(None),
):
    return database.get_posts(subreddit, start, end, sort, limit, q)

@app.get("/stats")
async def stats():
    return database.get_stats()
```

---

## Web UI Design

### `templates/index.html`

```
┌─────────────────────────────────────────────┐
│  RedFetcher v2                    [🔍 Search] │
├─────────────────────────────────────────────┤
│                                             │
│  📊 Total: 12,345 posts across 4 subreddits │
│                                             │
├─────────────────────────────────────────────┤
│  programming: 8,201 posts  [Scrape] [📋]    │
│  python:      3,142 posts  [Scrape] [📋]    │
│  unixporn:    987 posts    [Scrape] [📋]    │
│  linux:       15 posts     [Scrape] [📋]    │
├─────────────────────────────────────────────┤
│  [View All Posts →]                         │
│                                             │
│  Footer                                       │
└─────────────────────────────────────────────┘
```

- Header with app title
- Summary stats (total posts, total subreddits)
- Each subreddit row: name, post count, last updated, Scrape button
- Link to `/posts` for browsing

### `templates/posts.html`

```
┌─────────────────────────────────────────────┐
│  Posts                     [← Back]         │
├─────────────────────────────────────────────┤
│  [Subreddit ▼] [From ▼] [To ▼] [Sort ▼]    │
│  [Search title...] [Limit: 100 ▼]           │
│  [Apply Filters]                            │
├─────────────────────────────────────────────┤
│  ┌───────────────────────────────────────┐  │
│  │ [Title Link]  r/programming  ↑142  💬89 │  │
│  └───────────────────────────────────────┘  │
│  ┌───────────────────────────────────────┐  │
│  │ [Title Link]  r/python  ↑98  💬34     │  │
│  └───────────────────────────────────────┘  │
│  ...                                        │
│                                             │
│  Footer                                       │
└─────────────────────────────────────────────┘
```

- Filter bar: subreddit dropdown, date range inputs, sort dropdown, title search, limit selector
- Post cards: title (link to Reddit), subreddit badge, score, comment count, date
- All filters applied via query params (URL shareable)

---

## Filter/Sort Query Mapping

| UI Control | SQLite WHERE Clause |
|------------|-------------------|
| Subreddit dropdown | `WHERE subreddit = ?` |
| Date range start | `AND created_utc >= ?` (Unix timestamp) |
| Date range end | `AND created_utc <= ?` (Unix timestamp) |
| Title search | `AND title LIKE ?` |
| Sort: score desc | `ORDER BY score DESC` |
| Sort: score asc | `ORDER BY score ASC` |
| Sort: comments desc | `ORDER BY num_comments DESC` |
| Sort: comments asc | `ORDER BY num_comments ASC` |多项 |
| Sort: newest | `ORDER BY created_utc DESC` |
| Sort: oldest | `ORDER BY created_utc ASC` |

---

## Requirements (`requirements.txt`)

```
fastapi
uvicorn[standard]
arcshiftwrap
jinja2
```

---

## `.gitignore`

```
*.db
__pycache__/
*.pyc
.env
venv/
.venv/
*.sqlite
```

---

## Execution Instructions

### Setup
```bash
cd redfetcher-v2
pip install -r requirements.txt
```

### Run
```bash
python server.py
# Server starts at http://localhost:8000
```

### First Scrape
1. Open `http://localhost:8000`
2. Click "Scrape" for any subreddit
3. First run fetches ALL posts from 2005 to present
4. Duration depends on subreddit size and request rate

### Resume After Interruption
1. Re-open `http://localhost:8000`
2. Click "Scrape" again
3. Scraper resumes from `MAX(created_utc)` in the database
4. `INSERT OR IGNORE` skips already-stored posts
5. No duplicates, no data loss

---

## Known Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Arctic Shift API downtime | Scraper retries with backoff; user can retry later |
| Timeouts on dense time windows | `ArcticShiftClient` has built-in retry; re-run to continue |
| `arcshiftwrap` is new (v0.4.1) | Test thoroughly; fall back to raw HTTP if needed |
| Large subreddits take hours | Scrape can run overnight; resume works |
| `after=0` returns HTTP 400 | `collect_posts_by_windows` handles this internally |
| Import path confusion | Import from `arcshiftwrap.arctic_shift`, not `arcshiftwrap` |
| `start`/`end` must be datetime objects | Configured in code, not strings |

---

## Notes on `arcshiftwrap` Gotchas (verified via live testing)

1. **Import path**: `from arcshiftwrap.arctic_shift import collect_posts_by_windows` — NOT `from arcshiftwrap import collect_posts_by_windows`
2. **Datetime objects**: `start` and `end` must be `datetime` objects, not strings
3. **Max limit**: 100 per request (1000 returns HTTP 400)
4. **Sort order**: `asc` is ~10x faster than `desc`
5. **Response format**: `{"data": [...]}` — flat list of post dicts
6. **Pagination**: `collect_posts_by_windows` handles all cursor pagination internally
7. **`after=0` bug**: Returns HTTP 400 — omit entirely on first page
8. **Keyword search**: `title`, `selftext`, `query` only work alongside `author` or `subreddit`
9. **Rate headers**: `X-RateLimit-Remaining` and `X-RateLimit-Reset` present in responses
10. **No uptime SLA**: Community-maintained, free, no guarantees
