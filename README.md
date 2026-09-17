# RedFetcher v2

A local web application that archives Reddit posts from any subreddit since its creation. The scraper pulls data from the Arctic Shift API and stores it in SQLite. The web UI lets you browse, filter, and sort all archived posts.

## Architecture

- **`scraper.py`** — Standalone Python script. Fetches posts from Arctic Shift and stores them in SQLite. Run independently: `python scraper.py` or `python scraper.py <subreddit>`.
- **`server.py`** — Standalone FastAPI web app. Reads from the SQLite database and serves a filtered, sortable post browser at `http://localhost:8000`. Run independently: `python server.py`.
- **`sql.py`** — CLI tool for running SQL queries against `reddit_archive.db` (read-only, `SELECT` and `DELETE`).
- **Both are completely separate.** The scraper handles all scraping and DB writes. The server only reads from the DB and renders HTML.

## Requirements

- Python 3.10+
- `pip install -r requirements.txt`

## Quick Start

### 1. Install dependencies

```bash
cd redfetcher-v2
pip install -r requirements.txt
```

### 2. Scrape posts

Scrape all configured subreddits:

```bash
python scraper.py
```

Or scrape a specific subreddit (any subreddit works, not just the ones in config):

```bash
python scraper.py programming
```

The first run fetches all posts from the subreddit's earliest available date to present (~1-2 min/subreddit). Subsequent runs resume from the most recently stored post — interruption does not cause data loss.

### 3. View posts in the browser

```bash
python server.py
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

### 4. Browse the database (optional)

Run SQL queries against the local SQLite database:

```bash
python sql.py "SELECT * FROM posts LIMIT 5"
python sql.py "SELECT subreddit, COUNT(*) FROM posts GROUP BY subreddit"
python sql.py "DELETE FROM posts"          # Clear all data and restart fresh
```

## Usage

### Scraper (`scraper.py`)

```bash
python scraper.py                  # Scrape all configured subreddits
python scraper.py unixporn         # Scrape a specific subreddit
```

The scraper:

- Connects to the Arctic Shift API (`arctic-shift.photon-reddit.com`)
- Starts from today and works backwards dynamically — no pre-scan phase
- Automatically widens time windows for sparse regions, narrows for dense ones
- Stores posts in `reddit_archive.db` using batch `INSERT OR IGNORE` (no duplicates)
- Resumes from the most recently stored post on subsequent runs
- Handles API timeouts with automatic retry/backoff (2 retries, 15s timeout)
- **One Ctrl+C stops the entire scrape** — no need to interrupt subreddit-by-subreddit

### Web App (`server.py`)

```bash
python server.py                   # Starts at http://127.0.0.1:8000
```

The web UI provides:

- **Subreddit list** with post counts and last-updated timestamps
- **Filter bar** with:
    - Subreddit dropdown
    - Date range (start/end)
    - Sort by score, comments, newest, oldest
    - Title keyword search
    - Result limit (50, 100, 200, 500)
- **Post cards** with title (clickable link to Reddit), subreddit badge, score, comment count, date
- Dark theme with red accent (`#b43131`), responsive design

### API Endpoints

| Endpoint         | Description                             |
| ---------------- | --------------------------------------- |
| `GET /`          | Home page with subreddit list and stats |
| `GET /posts`     | Filtered post listing (HTML)            |
| `GET /api/posts` | Filtered post listing (JSON)            |
| `GET /stats`     | Post counts per subreddit (JSON)        |

Query parameters for `/posts` and `/api/posts`:

| Parameter   | Type   | Default      | Description         |
| ----------- | ------ | ------------ | ------------------- |
| `subreddit` | string | all          | Filter by subreddit |
| `start`     | date   | none         | Start date filter   |
| `end`       | date   | none         | End date filter     |
| `sort`      | string | `score_desc` | Sort order          |
| `limit`     | int    | 100          | Max results         |
| `q`         | string | none         | Title search        |

Sort options: `score_desc`, `score_asc`, `comments_desc`, `comments_asc`, `newest`, `oldest`

## Configuration

Edit `config.py` to customize:

```python
SUBREDDITS = [
    "AIAssisted", "AIToolsPerformance", "ArtificialIntelligence", "fossdroid",
    "LocalLLaMA", "LocalLLM", "LowEndLocalAI", "ObsidianMD", "opencode",
    "opencodeCLI", "OpenModels", "opensource", "PiCodingAgent", "Rainmeter",
    "selfhosted", "termux", "unixporn", "vibecoding", "web_design"
]
API_BASE = "https://arctic-shift.photon-reddit.com/api"
REQUEST_DELAY = 0.35          # Seconds between API requests (~2.8 req/s)
CLIENT_TIMEOUT = 15           # API request timeout (reduced from 60)
MAX_RETRIES = 2               # Retry attempts on failure (reduced from 4)
BACKOFF_FACTOR = 2.0          # Exponential backoff factor
DB_PATH = "reddit_archive.db" # SQLite database file
START_YEAR = 2005             # Fallback year if no resume data found
POSTS_PER_REQUEST = 100       # Max allowed by API per request
SORT_ORDER = "asc"            # Faster than desc, chronological order
```

The scraper accepts **any** subreddit as a command-line argument, not just the ones in `SUBREDDITS`.

## Data Model

Posts are stored in SQLite with these fields:

| Field          | Description                         |
| -------------- | ----------------------------------- |
| `id`           | Reddit base36 post ID (primary key) |
| `url`          | Reddit permalink (unique)           |
| `title`        | Post title                          |
| `score`        | Upvote count                        |
| `num_comments` | Comment count                       |
| `created_utc`  | Unix timestamp (for date filtering) |
| `subreddit`    | Subreddit name                      |
| `fetched_at`   | When the record was inserted        |

Resume logic: `SELECT MAX(created_utc) FROM posts WHERE subreddit = ?` determines where to resume. `INSERT OR IGNORE` on the `url` field prevents duplicates.

## Data Source

**Arctic Shift** (`arctic-shift.photon-reddit.com`) — a community-maintained archive of the entire Reddit history back to 2005. No API key required. Free.

- Python client: `arcshiftwrap` (`pip install arcshiftwrap`)
- Endpoint: `GET /api/posts/search`
- Max 100 posts per request
- `sort=asc` is ~10x faster than `sort=desc`

### Known limitations

- No uptime SLA (community-maintained)
- Timeouts on dense time windows (~every 7,000 posts)
- `after=0` returns HTTP 400 (omit entirely on first page)
- Rate limit headers present (`X-RateLimit-Remaining`, `X-RateLimit-Reset`)

## Project Structure

```text
redfetcher-v2/
├── config.py          # Settings: subreddit list, API config, request delay
├── database.py        # SQLite init, batch insert (executemany), query helpers
├── scraper.py         # Arctic Shift scraping logic (standalone)
├── server.py          # FastAPI web app (standalone)
├── sql.py             # CLI SQL query tool for the database
├── templates/
│   ├── index.html     # Landing page: subreddit list and stats
│   └── posts.html     # Post listing with filters/sort
├── requirements.txt
├── .gitignore
├── reddit_archive.db  # SQLite database (created on first scrape)
├── plan.md            # Detailed plan and research notes
└── README.md          # This file
```

## Troubleshooting

- **API timeouts**: Arctic Shift is community-maintained. Re-run the scraper; it has built-in retry/backoff.
- **No posts showing**: Run `python scraper.py` first, then `python server.py`.
- **Import error**: Ensure `arcshiftwrap` is installed (`pip install arcshiftwrap`). Import path is `arcshiftwrap.arctic_shift`, not `arcshiftwrap`.
- **Database locked**: Close any other connections to `reddit_archive.db`.
- **Slow scraping**: The first run for a new subreddit takes 1-2 minutes to find the earliest post date. Subsequent runs resume from where they left off.
- **Ctrl+C stops the entire scrape**: No need to interrupt subreddit-by-subreddit.

## Development Notes

### Scraping Flow

1. Check `MAX(created_utc)` for the subreddit in the database
2. If found, resume from that date (fast — only fetches new posts)
3. If not found, start from today and work backwards dynamically
4. Automatically widens time windows for empty regions, narrows for dense ones
5. Stops after 2 consecutive empty windows (no posts found)
6. Batch inserts using `executemany` for fast database writes

### URL Handling

The template (`templates/posts.html`) checks if `post.url` already starts with `http` before prepending `https://www.reddit.com`. The Arctic Shift API returns full URLs, so no prefix is added.
