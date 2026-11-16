"""Arctic Shift scraper with cursor pagination and adaptive pacing."""

import logging
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus

import requests
from arcshiftwrap.arctic_shift import ArcticShiftClient, normalize_response

import config
import database
from logging_setup import IST, setup_logging


SUBREDDIT_RE = re.compile(r"^[a-zA-Z0-9_]{2,21}$")
FIELDS = ["id", "title", "url", "score", "num_comments", "created_utc", "subreddit"]

MIN_REQUEST_INTERVAL = 0.5
MAX_REQUEST_INTERVAL = 4.0
MAX_ATTEMPTS = 5
BACKOFF_BASE = 1.0
BACKOFF_CAP = 8.0
RATE_LIMIT_CAP = 60.0
PROGRESS_INTERVAL = 15.0
INSERT_BATCH_ROWS = 500
INSERT_FLUSH_SECONDS = 3.0

log = logging.getLogger("redfetcher")

STATUS_LABELS = {
    400: "bad request",
    401: "unauthorized",
    403: "forbidden",
    404: "not found",
    422: "unprocessable",
    429: "rate limited",
    500: "server error",
    502: "bad gateway",
    503: "service unavailable",
    504: "gateway timeout",
    522: "connection timed out",
    523: "origin unreachable",
    530: "origin DNS error",
}


def _status_label(response):
    label = STATUS_LABELS.get(response.status_code)
    if label:
        return f"{response.status_code} {label}"
    try:
        reason = HTTPStatus(response.status_code).phrase
    except ValueError:
        reason = response.reason or ""
    return f"{response.status_code} {reason}".strip()


def _api_error(response):
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, str) and error.strip():
            return " ".join(error.split())[:200]
    text = " ".join((response.text or "").split())
    return text[:200] or "<empty>"


class ScrapeError(RuntimeError):
    pass


def _api_time(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _log_time(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")


def _fmt_duration(seconds):
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _as_ts(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _backoff(attempt, jitter):
    return min(BACKOFF_BASE * (2 ** (attempt - 1)), BACKOFF_CAP) + random.uniform(
        0, jitter
    )


def _open_connection():
    conn = database.get_connection()
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-65536")
    return conn


class Pacer:
    """Adaptive interval between request starts plus throttle state."""

    def __init__(self, min_interval=MIN_REQUEST_INTERVAL):
        self.min_interval = min_interval
        self.interval = min_interval
        self.last_request = 0.0
        self.throttles = 0

    def wait(self):
        delay = self.last_request + self.interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()

    def on_success(self):
        self.interval = max(self.min_interval, self.interval * 0.85)

    def _slow_down(self):
        self.throttles += 1
        self.interval = min(
            MAX_REQUEST_INTERVAL,
            max(self.interval * 1.6, self.min_interval * 2),
        )

    def on_throttle(self):
        self._slow_down()

    def on_rate_limit(self, response):
        self._slow_down()
        return self._rate_limit_wait(response)

    @staticmethod
    def _rate_limit_wait(response):
        now_ms = int(time.time() * 1000)
        candidates = []
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                candidates.append(float(retry_after))
            except ValueError:
                pass
        reset_at = response.headers.get("X-RateLimit-Reset-At")
        if reset_at and reset_at.isdigit():
            candidates.append(max(0.0, (int(reset_at) - now_ms) / 1000.0))
        reset = response.headers.get("X-RateLimit-Reset")
        if reset and reset.isdigit():
            candidates.append(float(reset))
        wait = max(candidates) if candidates else 30.0
        return min(max(wait, 1.0), RATE_LIMIT_CAP)


class CursorClient(ArcticShiftClient):
    """ArcticShiftClient with short throttle retries and 429 header handling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pacer = Pacer()
        self.requests = 0

    def request(self, endpoint, params=None):
        url = f"{self.base_url}{endpoint}"
        clean_params = self._clean(params)
        subreddit = clean_params.get("subreddit", "-")

        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.requests += 1
            self.pacer.wait()
            try:
                response = self.session.get(
                    url, params=clean_params, timeout=self.timeout
                )
            except requests.RequestException as exc:
                delay = _backoff(attempt, 0.4)
                if attempt >= MAX_ATTEMPTS:
                    log.error(
                        "[%s] network error - gave up after %d attempts: %s",
                        subreddit,
                        MAX_ATTEMPTS,
                        exc,
                    )
                    raise ScrapeError(
                        f"network error after {MAX_ATTEMPTS} attempts: {exc}"
                    ) from exc
                log.warning(
                    "[%s] network error (%s) - retrying in %.1fs (attempt %d/%d)",
                    subreddit,
                    type(exc).__name__,
                    delay,
                    attempt,
                    MAX_ATTEMPTS,
                )
                time.sleep(delay)
                continue

            status = response.status_code

            if status == 429:
                delay = self.pacer.on_rate_limit(response) + random.uniform(0, 0.5)
                if attempt >= MAX_ATTEMPTS:
                    log.error(
                        "[%s] 429 rate limited - gave up after %d attempts",
                        subreddit,
                        MAX_ATTEMPTS,
                    )
                    raise ScrapeError("429 rate limited")
                log.warning(
                    "[%s] 429 rate limited - retrying in %.1fs (attempt %d/%d)",
                    subreddit,
                    delay,
                    attempt,
                    MAX_ATTEMPTS,
                )
                time.sleep(delay)
                continue

            if status == 422:
                error = _api_error(response)
                if "timeout" in error.lower() or "slow down" in error.lower():
                    self.pacer.on_throttle()
                    delay = _backoff(attempt, 0.5)
                    if attempt >= MAX_ATTEMPTS:
                        log.error(
                            "[%s] 422 slow down - gave up after %d attempts",
                            subreddit,
                            MAX_ATTEMPTS,
                        )
                        raise ScrapeError(
                            f"422 slow down after {MAX_ATTEMPTS} attempts"
                        )
                    log.warning(
                        "[%s] 422 slow down (%s) - retrying in %.1fs "
                        "(attempt %d/%d)",
                        subreddit,
                        error,
                        delay,
                        attempt,
                        MAX_ATTEMPTS,
                    )
                    time.sleep(delay)
                    continue
                log.error(
                    "[%s] 422 request rejected (%s)", subreddit, error
                )
                raise ScrapeError(f"422 request rejected: {error}")

            if status in {500, 502, 503, 504, 522, 523, 530}:
                delay = _backoff(attempt, 0.4)
                label = _status_label(response)
                if attempt >= MAX_ATTEMPTS:
                    log.error(
                        "[%s] %s - gave up after %d attempts",
                        subreddit,
                        label,
                        MAX_ATTEMPTS,
                    )
                    raise ScrapeError(label)
                log.warning(
                    "[%s] %s - retrying in %.1fs (attempt %d/%d)",
                    subreddit,
                    label,
                    delay,
                    attempt,
                    MAX_ATTEMPTS,
                )
                time.sleep(delay)
                continue

            if status >= 400:
                label = _status_label(response)
                error = _api_error(response)
                log.error("[%s] %s - %s", subreddit, label, error)
                raise ScrapeError(f"{label}: {error}")

            try:
                data = response.json()
            except ValueError as exc:
                log.error(
                    "[%s] invalid JSON response (status %d)", subreddit, status
                )
                raise ScrapeError(f"invalid JSON (status {status})") from exc

            self.pacer.on_success()
            return data

        raise ScrapeError(f"unexpected failure: {url}")

    def check_health(self, subreddit):
        """One tiny probe; 2xx+JSON, 422 and 429 all count as reachable."""
        url = f"{self.base_url}/api/posts/search"
        params = {"subreddit": subreddit, "limit": 1, "fields": "id"}
        try:
            response = self.session.get(
                url, params=self._clean(params), timeout=self.timeout
            )
        except requests.RequestException as exc:
            return False, f"{type(exc).__name__}: {exc}"

        status = response.status_code
        if 200 <= status < 300:
            try:
                response.json()
            except ValueError:
                return False, "invalid JSON"
            return True, _status_label(response)
        if status in (422, 429):
            return True, _status_label(response)
        return False, f"{_status_label(response)}: {_api_error(response)}"


def _valid_post_row(post, index):
    """Validate a post, falling back to its permalink when url is empty."""
    if not isinstance(post, dict):
        log.warning("Skipping malformed post at batch index %d: %r", index, post)
        return None

    post_id = post.get("id")
    subreddit = post.get("subreddit")
    if not isinstance(post_id, str) or not post_id.strip():
        log.warning("Skipping post at batch index %d: invalid id %r", index, post_id)
        return None
    if not isinstance(subreddit, str) or not subreddit.strip():
        log.warning("Skipping post %r: invalid subreddit %r", post_id, subreddit)
        return None

    post_id = post_id.strip()
    subreddit = subreddit.strip()
    url = post.get("url")
    if isinstance(url, str) and url.strip():
        url = url.strip()
    else:
        url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/"
        log.debug("Post %r has no url; using permalink %s", post_id, url)

    return (
        post_id,
        url,
        post.get("title"),
        post.get("score"),
        post.get("num_comments"),
        post.get("created_utc"),
        subreddit,
    )


class ScrapeWriter:
    """Buffered inserts; checkpoint advanced atomically with the insert batch."""

    def __init__(self, conn):
        self.conn = conn
        self.buffer = []
        self.last_flush = time.monotonic()
        self.inserted = 0

    def add(self, posts, subreddit, checkpoint_ts):
        for index, post in enumerate(posts):
            row = _valid_post_row(post, index)
            if row is not None:
                self.buffer.append(row)
        if (
            len(self.buffer) >= INSERT_BATCH_ROWS
            or (time.monotonic() - self.last_flush) >= INSERT_FLUSH_SECONDS
        ):
            self.flush(subreddit, checkpoint_ts)

    def flush(self, subreddit, checkpoint_ts):
        if not self.buffer and checkpoint_ts is None:
            self.last_flush = time.monotonic()
            return

        if self.buffer:
            cursor = self.conn.executemany(
                "INSERT OR IGNORE INTO posts "
                "(id, url, title, score, num_comments, created_utc, subreddit) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                self.buffer,
            )
            self.inserted += max(0, cursor.rowcount)
            self.buffer.clear()

        if checkpoint_ts is not None:
            self.conn.execute(
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
                (subreddit, checkpoint_ts),
            )

        self.conn.commit()
        self.last_flush = time.monotonic()


class SubStats:
    def __init__(self, subreddit, start, end):
        self.subreddit = subreddit
        self.start = start
        self.end = end
        self.cursor = start
        self.started = time.monotonic()
        self.last_progress = self.started
        self.requests = 0
        self.inserted = 0
        self.throttles = 0

    def elapsed(self):
        return time.monotonic() - self.started

    def maybe_progress(self):
        now = time.monotonic()
        if now - self.last_progress < PROGRESS_INTERVAL:
            return
        self.last_progress = now

        total_span = max(1.0, (self.end - self.start).total_seconds())
        covered = max(0.0, (self.cursor - self.start).total_seconds())
        pct = min(100.0, 100.0 * covered / total_span)
        elapsed = self.elapsed()
        if covered > 0 and total_span > covered:
            eta = (total_span - covered) * (elapsed / covered)
            eta_text = _fmt_duration(eta)
        else:
            eta_text = "--"

        log.info(
            "[%s] %d%% complete - %d inserted, ETA %s, %d throttles",
            self.subreddit,
            int(pct),
            self.inserted,
            eta_text,
            self.throttles,
        )


def find_start_year(subreddit, client):
    """Return the earliest post's year using a single ascending query."""
    posts = normalize_response(
        client.search_posts(
            subreddit=subreddit,
            limit=1,
            sort="asc",
            fields=["created_utc"],
        )
    )
    created = _as_ts(posts[0].get("created_utc")) if posts else None
    if created is None:
        log.warning(
            "[%s] No posts found; starting from %s", subreddit, config.START_YEAR
        )
        return config.START_YEAR
    try:
        year = max(
            datetime.fromtimestamp(created, timezone.utc).year, config.START_YEAR
        )
    except (ValueError, OverflowError, OSError):
        return config.START_YEAR
    log.info("[%s] First post found in %s", subreddit, year)
    return year


def get_scrape_start(subreddit, client):
    checkpoint = database.get_scrape_state(subreddit)
    if checkpoint is not None:
        checkpoint_dt = datetime.fromtimestamp(checkpoint, timezone.utc)
        resume_dt = checkpoint_dt - timedelta(hours=config.RESUME_OVERLAP_HOURS)
        log.info(
            "[%s] Resuming from checkpoint %s with a %s-hour overlap",
            subreddit,
            _log_time(checkpoint_dt),
            config.RESUME_OVERLAP_HOURS,
        )
        return resume_dt

    start_year = find_start_year(subreddit, client)
    log.info(
        "[%s] No checkpoint found; starting a full backfill from %s",
        subreddit,
        start_year,
    )
    return datetime(start_year, 1, 1, tzinfo=timezone.utc)


def _fetch_boundary_second(client, subreddit, ts):
    """Fetch one exact second from both ends; only used on detected overflow."""
    after = _api_time(datetime.fromtimestamp(ts - 1, timezone.utc))
    before = _api_time(datetime.fromtimestamp(ts + 1, timezone.utc))
    merged = {}
    for sort in ("asc", "desc"):
        data = client.search_posts(
            subreddit=subreddit,
            after=after,
            before=before,
            limit="auto",
            sort=sort,
            fields=FIELDS,
        )
        for post in normalize_response(data):
            post_id = post.get("id")
            if post_id and post_id not in merged:
                merged[post_id] = post
    if len(merged) >= 1000:
        log.warning(
            "[%s] same-second flood at %s: merged %d posts from both ends; "
            "if this second holds more than two pages, some may be skipped",
            subreddit,
            _log_time(datetime.fromtimestamp(ts, timezone.utc)),
            len(merged),
        )
    return list(merged.values())


def scrape_subreddit(client, conn, subreddit, run_end):
    start = get_scrape_start(subreddit, client)
    if start >= run_end:
        log.info("[%s] Checkpoint is current - nothing to scrape", subreddit)
        return 0

    stats = SubStats(subreddit, start, run_end)
    request_start = client.requests
    throttle_start = client.pacer.throttles
    writer = ScrapeWriter(conn)

    after_ts = int(start.timestamp()) - 1
    before = _api_time(run_end + timedelta(seconds=1))
    run_end_ts = int(run_end.timestamp())
    last_max_ts = None
    last_checkpoint = None
    last_logged_inserted = 0
    completed = False

    log.info(
        "[%s] Scraping %s to %s",
        subreddit,
        _log_time(start),
        _log_time(run_end),
    )

    try:
        while True:
            data = client.search_posts(
                subreddit=subreddit,
                after=_api_time(datetime.fromtimestamp(after_ts, timezone.utc)),
                before=before,
                limit="auto",
                sort="asc",
                fields=FIELDS,
            )
            posts = normalize_response(data)

            if not posts:
                completed = True
                break

            ts_values = [
                ts
                for ts in (_as_ts(p.get("created_utc")) for p in posts)
                if ts is not None
            ]
            if not ts_values:
                log.warning(
                    "[%s] page has no usable created_utc; stopping without "
                    "advancing the checkpoint to now",
                    subreddit,
                )
                break

            max_ts = int(max(ts_values))
            stats.cursor = datetime.fromtimestamp(max_ts, timezone.utc)

            if last_max_ts is not None and max_ts == last_max_ts:
                log.debug(
                    "[%s] no time progress at %s; merging the boundary second",
                    subreddit,
                    _log_time(stats.cursor),
                )
                writer.add(posts, subreddit, max_ts)
                boundary = _fetch_boundary_second(client, subreddit, max_ts)
                writer.add(boundary, subreddit, max_ts)
                after_ts = max_ts
                last_max_ts = None
            else:
                last_checkpoint = max_ts
                writer.add(posts, subreddit, max_ts)
                last_max_ts = max_ts
                after_ts = max_ts - 1

            stats.inserted = writer.inserted
            stats.requests = client.requests - request_start
            stats.throttles = client.pacer.throttles - throttle_start
            if stats.inserted > last_logged_inserted:
                log.info(
                    "[%s] %d post%s inserted - up to %s",
                    subreddit,
                    stats.inserted,
                    "" if stats.inserted == 1 else "s",
                    _log_time(stats.cursor),
                )
                last_logged_inserted = stats.inserted
            stats.maybe_progress()

            if after_ts >= run_end_ts:
                completed = True
                break
    except (KeyboardInterrupt, Exception):
        writer.flush(subreddit, last_checkpoint)
        raise

    checkpoint_ts = run_end_ts if completed else last_checkpoint
    writer.flush(subreddit, checkpoint_ts)

    stats.inserted = writer.inserted
    stats.requests = client.requests - request_start
    stats.throttles = client.pacer.throttles - throttle_start
    log.info(
        "[%s] finished in %s - %d inserted, %d requests, %d throttles",
        subreddit,
        _fmt_duration(stats.elapsed()),
        stats.inserted,
        stats.requests,
        stats.throttles,
    )
    return stats.inserted


def make_client():
    return CursorClient(timeout=config.CLIENT_TIMEOUT)


def ensure_api_health(client, subreddit):
    log.info("Checking Arctic Shift health via r/%s ...", subreddit)
    ok, detail = client.check_health(subreddit)
    if not ok:
        log.error("Arctic Shift is unreachable (%s); aborting", detail)
    return ok


def scrape_all():
    if not config.SUBREDDITS:
        log.error("No subreddits configured")
        return 1

    conn = _open_connection()
    client = make_client()

    started = time.monotonic()
    total_inserted = 0
    ok = 0
    failed = []

    try:
        if not ensure_api_health(client, config.SUBREDDITS[0]):
            return 1

        log.info("Starting scrape for %d subreddits", len(config.SUBREDDITS))

        for index, subreddit in enumerate(config.SUBREDDITS, 1):
            run_end = datetime.now(timezone.utc)
            log.info("[%d/%d] %s", index, len(config.SUBREDDITS), subreddit)
            try:
                total_inserted += scrape_subreddit(
                    client, conn, subreddit, run_end
                )
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                log.error("[%s] skipped - %s", subreddit, exc)
                failed.append(subreddit)
                continue
            ok += 1

        log.info(
            "Scrape finished in %s - %d inserted, %d ok, %d skipped",
            _fmt_duration(time.monotonic() - started),
            total_inserted,
            ok,
            len(failed),
        )
        if failed:
            log.warning("Skipped subreddits: %s", ", ".join(failed))
            return 1
        return 0
    except KeyboardInterrupt:
        log.warning(
            "Scrape interrupted after %s - %d inserted, %d ok, %d skipped",
            _fmt_duration(time.monotonic() - started),
            total_inserted,
            ok,
            len(failed),
        )
        return 130
    finally:
        conn.close()


if __name__ == "__main__":
    setup_logging()
    database.init_db()
    log.info("Database initialized at %s", config.DB_PATH)

    try:
        if len(sys.argv) > 1:
            subreddit = sys.argv[1]
            if not SUBREDDIT_RE.match(subreddit):
                log.error(
                    "Invalid subreddit name: %r (2-21 chars: letters, digits, _)",
                    subreddit,
                )
                sys.exit(1)

            client = make_client()
            if not ensure_api_health(client, subreddit):
                sys.exit(1)

            conn = _open_connection()
            try:
                scrape_subreddit(client, conn, subreddit, datetime.now(timezone.utc))
            finally:
                conn.close()
        else:
            sys.exit(scrape_all())
    except KeyboardInterrupt:
        log.info("Scrape stopped by user.")
        sys.exit(130)
    except Exception as exc:
        log.error("Scrape failed: %s", exc)
        sys.exit(1)
