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

The first run performs a full backfill from the subreddit's earliest available date. Saturated 30-day windows are split into smaller UTC windows until every window is complete. Subsequent runs resume from a durable per-subreddit checkpoint and recheck a 48-hour overlap; interruptions do not advance the checkpoint past an incomplete window.

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
- Uses exact UTC timestamps and sequential requests with a one-second delay
- Starts with 30-day windows and splits any window that returns the 100-post API cap
- Stops safely with an explicit error if a one-hour window still saturates
- Stores posts in `reddit_archive.db` using batch `INSERT OR IGNORE` (no duplicates)
- Persists a durable per-subreddit checkpoint after every complete window
- Rechecks the last 48 hours on resumed runs to catch recently archived posts
- Handles API timeouts, rate limits, and service errors with status-aware retry/backoff
- Waits 20 seconds for query-timeout `422` responses, then splits the window if needed
- Logs HTTP status, response summary, and rate-limit reset headers for API failures
- Stops the run on an unrecoverable API error instead of silently skipping a window
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
REQUEST_DELAY = 1.0           # Minimum delay after each API request
CLIENT_TIMEOUT = 15           # API request timeout
MAX_RETRIES = 2               # Retry attempts on failure
BACKOFF_FACTOR = 2.0          # Exponential backoff factor
DB_PATH = "reddit_archive.db" # SQLite database file
START_YEAR = 2005             # Earliest year checked during full backfill
POSTS_PER_REQUEST = 100       # Maximum requested from the API
MIN_SCRAPE_WINDOW_HOURS = 1   # Stop splitting a saturated window below this width
QUERY_TIMEOUT_RETRY_DELAY = 20 # Wait before retrying a query-timeout 422
RESUME_OVERLAP_HOURS = 48     # Recheck recently archived posts on resume
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
| `post_id`    | Reference to `posts.id` (unique, cascade delete) |
| `created_at` | When the bookmark was created |

### Scrape State Table

| Field        | Description                                      |
| ------------ | ------------------------------------------------ |
| `subreddit`  | Subreddit name (primary key)                    |
| `next_start` | First timestamp not yet covered by a full window |
| `updated_at` | When the checkpoint was last advanced           |

The scraper state is stored in the `scrape_state` table. `next_start` is the first timestamp not yet covered by a complete window; it advances only after the corresponding posts have been inserted. `INSERT OR IGNORE` on the `url` field prevents duplicates from boundary overlap and resumed requests.

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
├── database.py        # SQLite init, batch insert, query helpers, bookmarks, scrape state
├── scraper.py         # Arctic Shift scraping logic (standalone)
├── server.py          # FastAPI web app — routes, templates, bookmarks (standalone)
├── sql.py             # CLI SQL query tool for reddit_archive.db
├── fonts/             # Self-hosted Sora, DM Sans, and Font Awesome assets
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

- **API timeouts**: Arctic Shift is community-maintained. Re-run the scraper; it has built-in retry/backoff and reports the HTTP status, response summary, and rate-limit reset when the API is unavailable.
- **No posts showing**: Run `python scraper.py` first, then `python server.py`.
- **Import error**: Ensure `arcshiftwrap` is installed (`pip install arcshiftwrap`). Import path is `arcshiftwrap.arctic_shift`, not `arcshiftwrap`.
- **Database locked**: Close any other connections to `reddit_archive.db`.
- **Slow scraping**: Full backfills make more API requests for dense subreddits; the scraper stays sequential and one request per second. Resumed runs recheck only the checkpoint plus the configured overlap.
- **Ctrl+C stops the entire scrape**: No need to interrupt subreddit-by-subreddit.
- **Bookmarks not working**: The bookmarks feature requires the server to be running. Use the bookmark button (🪖) on any post card to toggle bookmarks.

## Development Notes

### Scraping Flow

1. Read the durable `scrape_state.next_start` checkpoint for the subreddit
2. If a checkpoint exists, resume 48 hours before it to catch late-arriving archive data
3. If no checkpoint exists, find the earliest year with posts via yearly scan (`find_start_year`)
4. Request 30-day UTC windows with `limit=100`
5. Split every saturated window recursively until each child is below the limit
6. Stop safely if a one-hour window still returns the limit
7. Batch insert each complete result, then advance the checkpoint only after insertion
8. Stop the run on API failure without advancing past the failed window

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
- **Fonts**: Self-hosted Sora (display/headings) + DM Sans (body)
- **Layout**: 1200px centered container, sticky header, footer at bottom
- **Accessibility**: Skip-to-content link, ARIA labels, focus-visible outlines, `prefers-reduced-motion` support
