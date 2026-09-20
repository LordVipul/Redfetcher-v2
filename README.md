# RedFetcher v2

A local web application that archives Reddit posts from any subreddit since its creation. The scraper pulls data from the Arctic Shift API and stores it in SQLite. The FastAPI web UI lets you browse, filter, sort, and bookmark posts — all running locally with zero external dependencies beyond the data source.

## Architecture

- **`scraper.py`** — Standalone Python script. Fetches posts from Arctic Shift and stores them in SQLite. Run independently: `python scraper.py` or `python scraper.py <subreddit>`.
- **`server.py`** — Standalone FastAPI web app. Reads from the SQLite database and serves a filtered, sortable post browser at `http://localhost:8000`. Includes a bookmarks feature.
- **`database.py`** — SQLite init, batch insert (`executemany`), query helpers, and full bookmarks CRUD.
- **`sql.py`** — CLI tool for running read-only `SELECT` and `DELETE` queries against `reddit_archive.db`.
- **Both scraper and server are completely separate.** The scraper handles all scraping and DB writes. The server only reads from the DB and renders HTML.

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

The first run fetches all posts from the subreddit's earliest available date to present (~1–2 min/subreddit). Subsequent runs resume from the most recently stored post — interruption does not cause data loss.

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
python sql.py --watch "SELECT COUNT(*) FROM posts"   # Live monitoring
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

- **Subreddit list** with post counts on the home page (`/`)
- **Bookmarks page** (`/bookmarks`) — save and filter posts you want to keep
- **Filter bar** with:
    - Subreddit dropdown
    - Date range (start/end)
    - Sort by score, comments, newest, oldest
    - Title keyword search
    - Result limit (50, 100, 200, 500)
- **Post cards** with title (clickable link to Reddit), subreddit badge, score, comment count, date, and bookmark button
- Dark theme with red-orange accent (`#E85D3A`), responsive design
- Client-side live filtering, pagination with jump-to-page, and URL state preservation
- Keyboard shortcuts: `Ctrl+K` or `/` to focus search

### API Endpoints

| Endpoint                      | Description                             |
| ----------------------------- | --------------------------------------- |
| `GET /`                       | Home page with subreddit list and stats |
| `GET /posts`                  | Filtered post listing (HTML)            |
| `GET /api/posts`              | Filtered post listing (JSON)            |
| `GET /stats`                  | Post counts per subreddit (JSON)        |
| `GET /bookmarks`              | Bookmarked posts listing (HTML)         |
| `GET /api/bookmarks`          | Bookmarked posts listing (JSON)         |
| `POST /bookmarks/{post_id}`   | Add a bookmark                          |
| `DELETE /bookmarks/{post_id}` | Remove a bookmark                       |

Query parameters for `/posts`, `/api/posts`, `/bookmarks`, and `/api/bookmarks`:

| Parameter   | Type   | Default  | Description         |
| ----------- | ------ | -------- | ------------------- |
| `subreddit` | string | all      | Filter by subreddit |
| `start`     | date   | none     | Start date filter   |
| `end`       | date   | none     | End date filter     |
| `sort`      | string | `oldest` | Sort order          |
| `limit`     | int    | 50       | Max results         |
| `q`         | string | none     | Title search        |
| `page`      | int    | 1        | Page number         |

Sort options: `score_desc`, `score_asc`, `comments_desc`, `comments_asc`, `newest`, `oldest`

## Configuration

Edit `config.py` to customize:

```python
SUBREDDITS = [
    "AIAssisted", "AIToolsPerformance", "ArtificialInteligence", "fossdroid",
    "LocalLLaMA", "LocalLLM", "LowEndLocalAI", "ObsidianMD", "opencode",
    "opencodeCLI", "OpenModels", "opensource", "PiCodingAgent", "Rainmeter",
    "selfhosted", "termux", "unixporn", "vibecoding", "web_design"
]
REQUEST_DELAY = 0.35          # Seconds between API requests (~2.8 req/s)
CLIENT_TIMEOUT = 15           # API request timeout
MAX_RETRIES = 2               # Retry attempts on failure
BACKOFF_FACTOR = 2.0          # Exponential backoff factor
DB_PATH = "reddit_archive.db" # SQLite database file
START_YEAR = 2005             # Fallback year if no resume data found
POSTS_PER_REQUEST = 100       # Max allowed by API per request
```

The scraper accepts **any** subreddit as a command-line argument, not just the ones in `SUBREDDITS`.

## Data Model

### Posts Table

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

### Bookmarks Table

| Field        | Description                   |
| ------------ | ----------------------------- |
| `id`         | Auto-increment bookmark ID    |
| `post_id`    | Reference to post (unique)    |
| `created_at` | When the bookmark was created |

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
├── database.py        # SQLite init, batch insert, query helpers, bookmarks
├── scraper.py         # Arctic Shift scraping logic (standalone)
├── server.py          # FastAPI web app — routes, templates, bookmarks (standalone)
├── sql.py             # CLI SQL query tool for reddit_archive.db
├── templates/
│   ├── base.html      # Layout shell with header, nav, footer
│   ├── index.html     # Landing page: community card grid
│   ├── posts.html     # Post listing with filters/sort/pagination
│   ├── bookmarks.html # Bookmarked posts with filters/sort/pagination
│   └── static/
│       ├── css/style.css     # AMOLED black + red-orange design system
│       └── js/app.js         # Client-side filtering, pagination, bookmarks
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
- **Slow scraping**: The first run for a new subreddit takes 1–2 minutes to find the earliest post date. Subsequent runs resume from where they left off.
- **Ctrl+C stops the entire scrape**: No need to interrupt subreddit-by-subreddit.
- **Bookmarks not working**: The bookmarks feature requires the server to be running. Use the bookmark button (🪖) on any post card to toggle bookmarks.

## Development Notes

### Scraping Flow

1. Check `MAX(created_utc)` for the subreddit in the database
2. If found, resume from that date (fast — only fetches new posts)
3. If not found, find the earliest year with posts via yearly scan (`find_start_year`)
4. Automatically widens time windows for empty regions, narrows for dense ones
5. Stops after 2 consecutive empty windows (no posts found)
6. Batch inserts using `executemany` for fast database writes

### URL Handling

The templates check if `post.url` already starts with `http` before prepending `https://www.reddit.com`. The Arctic Shift API returns full URLs, so no prefix is added.

### Client-Side Features

- **Live filtering**: All filters (subreddit, sort, date range, search, limit) work via `fetch()` without full page reloads
- **URL state**: Filter state is preserved in the URL query string — shareable links
- **Pagination**: Page numbers, Prev/Next, and jump-to-page input
- **Bookmarks**: Toggle bookmarks with undo toast notifications; works on both `/posts` and `/bookmarks` pages
- **Keyboard shortcuts**: `Ctrl+K` or `/` focuses the search input
- **Responsive**: Works on desktop and mobile

### Design System

- **Theme**: AMOLED black (`#000000`) background, dark surface (`#0A0A0A`), red-orange accent (`#E85D3A`)
- **Fonts**: Sora (display/headings) + DM Sans (body)
- **Layout**: 1200px centered container, sticky header, footer at bottom
- **Accessibility**: Skip-to-content link, ARIA labels, focus-visible outlines, `prefers-reduced-motion` support
