# RedFetcher v2

A local web application that archives Reddit posts from any subreddit since its creation. The scraper pulls data from the Arctic Shift API and stores it in SQLite. The FastAPI web UI lets you browse, filter, sort, and bookmark posts — all running locally with zero external dependencies beyond the data source.

## Architecture

- **`scraper.py`** — Standalone Python script. Fetches posts from Arctic Shift and stores them in SQLite. Run independently: `python scraper.py` or `python scraper.py <subreddit>`.
- **`server.py`** — Standalone FastAPI web app. Reads from the SQLite database and serves a single-page, group-driven post browser at `http://localhost:8000`. Includes bookmarks and a dashboard. Never scrapes; reads posts, writes only bookmarks.
- **`config.py`** — `GROUPS` (sidebar order: key → short label + subreddits) and `SUBREDDITS` (derived from `GROUPS`, used by the scraper), plus API/delay settings.
- **`database.py`** — SQLite init, batch insert (`executemany`), query helpers (including multi-subreddit group queries), dashboard aggregations, and full bookmarks CRUD.
- **`sql.py`** — CLI tool for running ad-hoc SQL against `reddit_archive.db`. It executes whatever statement you pass, so it can `DELETE` or `UPDATE` as well as `SELECT`.
- **Both scraper and server are completely separate.** The scraper is the only thing that writes posts. The server never scrapes; it reads posts and writes only bookmarks.

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

Open [http://localhost:8000](http://localhost:8000) in your browser. The sidebar lists **All** plus every configured group; the main section shows that group's posts with the full filter bar (subreddit scoped to the group, sort, limit, min score/comments, dates, title search). **Bookmarks** sits at the bottom of the sidebar and swaps the main section to saved posts. The gear icon opens settings (dashboard link, hide sidebar, text size, reduce motion). `/dashboard` shows archive statistics.

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

- **Sidebar** with **All** plus every group from `config.GROUPS` (with live post counts); clicking a group loads only its subreddits' posts. **Bookmarks** at the bottom of the sidebar swaps the main section to saved posts
- **Filter bar** at the top of the main section with:
    - Subreddit dropdown (scoped to the active group; shows "All in <group>")
    - Sort by score, comments, newest, oldest (hidden in the bookmarks view — bookmarks are always oldest-added first)
    - Title keyword search
    - Minimum score and minimum comment count thresholds (independent, digits-only inputs)
    - Date range (start/end)
    - Result limit (50, 100, 200, 500)
- **Post cards** with title (clickable link to Reddit), subreddit badge (click to filter), score, comment count, date, and bookmark button
- **Bookmarks** with undo toast notifications; the sidebar count updates live
- **Dashboard** (`/dashboard`) — total posts/subreddits/bookmarks/score, posts-per-month timeline, top subreddits, per-group totals, top posts, and the full subreddit list
- **Settings menu** (gear icon) — dashboard link, hide/show sidebar, text size, reduce motion. All preferences persist in `localStorage`
- Dark theme with red-orange accent (`#E85D3A`), responsive design, client-side live filtering, pagination with jump-to-page, and URL state preservation (shareable links, e.g. `/?group=ai&sort=score_desc`)
- Keyboard shortcuts: `Ctrl+K` or `/` to focus search

### API Endpoints

| Endpoint                      | Description                             |
| ----------------------------- | --------------------------------------- |
| `GET /`                       | Main app: group feed or bookmarks view (HTML) |
| `GET /api/posts`              | Filtered post listing (JSON, supports `group`) |
| `GET /stats`                  | Post counts per subreddit (JSON)        |
| `GET /api/bookmarks`          | Bookmarked posts listing (JSON)         |
| `GET /dashboard`              | Dashboard page (HTML)                   |
| `POST /bookmarks/{post_id}`   | Add a bookmark                          |
| `DELETE /bookmarks/{post_id}` | Remove a bookmark                       |

Query parameters for `/`, `/api/posts`, and `/api/bookmarks`:

| Parameter      | Type   | Default  | Description                  |
| -------------- | ------ | -------- | ---------------------------- |
| `group`        | string | `all`    | Group key from `config.GROUPS` (feed views only) |
| `view`         | string | `feed`   | `feed` or `bookmarks` (`/` only) |
| `subreddit`    | string | all      | Filter by subreddit (within the group) |
| `start`        | date   | none     | Start date filter            |
| `end`          | date   | none     | End date filter              |
| `sort`         | string | `oldest` | Sort order (feed views only) |
| `limit`        | int    | 50       | Max results                  |
| `q`            | string | none     | Title search                 |
| `page`         | int    | 1        | Page number                  |
| `min_score`    | int    | none     | Minimum score (`>=`)         |
| `min_comments` | int    | none     | Minimum comment count (`>=`) |

Sort options: `score_desc`, `score_asc`, `comments_desc`, `comments_asc`, `newest`, `oldest`

`min_score` and `min_comments` are independent and combine with every other filter. Omitting
the parameter applies no threshold; sending `0` applies `>= 0`, which is a real filter and is
narrower than omitting it whenever a post has a negative score or comment count. Both reject
negative or non-numeric values with HTTP 400.

The filter bar labels them **Min score** and **Min comm**. Both inputs accept digits only —
non-numeric characters are rejected as you type and stripped from pasted text, keeping the
leading run of digits, so `1e3` becomes `1` and `12.5` becomes `12`.

`/posts` and `/bookmarks` still work as 302 redirects into `/` (preserving query parameters), so old links keep working.

## Configuration

Edit `config.py` to customize:

```python
GROUPS = {
    "ai": ("AI", [
        "AIAssisted", "AIToolsPerformance", "ArtificialInteligence", "learnmachinelearning", "LocalLLaMA", "LocalLLM", "LowEndLocalAI", "MachineLearning", "opencode", "opencodeCLI", "PiCodingAgent", "vibecoding",
    ]),
    "web": ("Web dev", ["css", "Frontend", "javascript", "js", "learnjavascript", "node", "webdev"]),
    "design": ("Design", ["accessibility", "design_critiques", "DesignPorn", "graphic_design", "Typography", "UI_Design", "userexperience", "web_design"]),
    ...                             # 18 groups total, in sidebar order
}
SUBREDDITS = [sub for _, subs in GROUPS.values() for sub in subs]  # derived, used by the scraper
REQUEST_DELAY = 1.0           # Minimum delay after each API request
CLIENT_TIMEOUT = 30           # API request timeout
MAX_RETRIES = 2               # Retry attempts on failure
BACKOFF_FACTOR = 2.0          # Exponential backoff factor
DB_PATH = "reddit_archive.db" # SQLite database file
START_YEAR = 2005             # Earliest year checked during full backfill
POSTS_PER_REQUEST = 100       # Maximum requested from the API
MIN_SCRAPE_WINDOW_HOURS = 1   # Stop splitting a saturated window below this width
QUERY_TIMEOUT_RETRY_DELAY = 20 # Wait before retrying a query-timeout 422
RESUME_OVERLAP_HOURS = 48     # Recheck recently archived posts on resume
```

Groups are ordered (dict insertion order = sidebar order) and subreddits within a group are
alphabetical. `SUBREDDITS` is derived from `GROUPS`, so the scraper needs no changes when groups
change.

The scraper accepts **any** subreddit as a command-line argument, not just the ones in the groups.

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

| Field        | Description                                      |
| ------------ | ------------------------------------------------ |
| `id`         | Auto-increment bookmark ID                       |
| `post_id`    | Reference to `posts.id` (unique, cascade delete) |
| `created_at` | When the bookmark was created                    |

### Scrape State Table

| Field        | Description                                      |
| ------------ | ------------------------------------------------ |
| `subreddit`  | Subreddit name (primary key)                     |
| `next_start` | First timestamp not yet covered by a full window |
| `updated_at` | When the checkpoint was last advanced            |

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
├── config.py          # Groups (sidebar), derived subreddit list, API config, request delay
├── database.py        # SQLite init, batch insert, query helpers, group queries, dashboard, bookmarks, scrape state
├── scraper.py         # Arctic Shift scraping logic (standalone)
├── server.py          # FastAPI web app — routes, groups, bookmarks, dashboard (standalone)
├── sql.py             # CLI SQL query tool for reddit_archive.db
├── fonts/             # Self-hosted Sora, DM Sans, and Font Awesome assets
├── templates/
│   ├── base.html      # Layout shell: header with settings menu, sidebar rail, footer
│   ├── feed.html      # Main feed: filter bar + post list + pagination (feed & bookmarks views)
│   ├── dashboard.html # Stats: cards, monthly timeline, top subreddits, groups, top posts
│   └── static/
│       ├── css/style.css     # AMOLED black + red-orange design system
│       └── js/app.js         # Sidebar groups, live filtering, pagination, bookmarks, prefs
├── requirements.txt
├── .gitignore
├── reddit_archive.db  # SQLite database (created on first scrape)
└── README.md          # This file
```

## Troubleshooting

- **API timeouts**: Arctic Shift is community-maintained. Re-run the scraper; it has built-in retry/backoff and reports the HTTP status, response summary, and rate-limit reset when the API is unavailable.
- **No posts showing**: Run `python scraper.py` first, then `python server.py`.
- **Import error**: Ensure `arcshiftwrap` is installed (`pip install arcshiftwrap`). Import path is `arcshiftwrap.arctic_shift`, not `arcshiftwrap`.
- **Database locked**: Close any other connections to `reddit_archive.db`.
- **Slow scraping**: Full backfills make more API requests for dense subreddits; the scraper stays sequential and one request per second. Resumed runs recheck only the checkpoint plus the configured overlap.
- **Ctrl+C stops the entire scrape**: No need to interrupt subreddit-by-subreddit.
- **Bookmarks not working**: The bookmarks feature requires the server to be running. Use the bookmark button on any post card to toggle bookmarks.

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

- **Group switching**: Sidebar buttons swap the active group without a page reload; the subreddit dropdown rebuilds to that group's subreddits
- **Live filtering**: All filters (subreddit, sort, date range, search, min score, min comm, limit) work via `fetch()` without full page reloads
- **URL state**: Group, view, and filter state live in the URL query string — shareable links (e.g. `/?group=ai&sort=score_desc&q=linux`)
- **Pagination**: Page numbers, Prev/Next, and jump-to-page input, with focus restoration after navigation
- **Bookmarks**: Toggle bookmarks with undo toast notifications; sidebar count updates live; bookmarks view removes unbookmarked rows in place
- **Preferences**: Hide sidebar, text size, and reduce motion persist in `localStorage`
- **Keyboard shortcuts**: `Ctrl+K` or `/` focuses the search input

### Design System

- **Theme**: AMOLED black (`#000000`) background, dark surface (`#0A0A0A`), red-orange accent (`#E85D3A`); active sidebar group is accent on white text
- **Fonts**: Self-hosted Sora (display/headings) + DM Sans (body)
- **Layout**: Header + sidebar rail (All + groups + Bookmarks) beside a 1200px content column; sticky sidebar and header; footer at bottom
- **Accessibility**: Skip-to-content link, ARIA labels and pressed states, focus-visible outlines, screen-reader result announcer, `prefers-reduced-motion` support
