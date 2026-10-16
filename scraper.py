import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from arcshiftwrap.arctic_shift import ArcticShiftClient, split_time_range, normalize_response

import config
import database


SUBREDDIT_RE = re.compile(r"^[a-zA-Z0-9_]{3,21}$")


class ScrapeError(RuntimeError):
    pass


class QueryTimeoutError(ScrapeError):
    pass


class LoggingArcticShiftClient(ArcticShiftClient):
    def _body_summary(self, response):
        text = " ".join((response.text or "").split())
        if not text:
            return "<empty>"
        if len(text) > 400:
            return text[:397] + "..."
        return text

    def _retry_delay(self, attempt):
        return min(self.backoff_factor**attempt, 60.0)

    def _rate_limit_delay(self, response):
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return max(1, int(retry_after))
        return max(1, self._rate_limit_wait(response))

    def request(self, endpoint, params=None):
        url = f"{self.base_url}{endpoint}"
        clean_params = self._clean(params)
        max_attempts = max(1, self.max_retries)

        for attempt in range(1, max_attempts + 1):
            log.debug(
                "API request attempt=%d/%d method=GET url=%s params=%s",
                attempt,
                max_attempts,
                url,
                clean_params,
            )
            try:
                response = self.session.get(url, params=clean_params, timeout=self.timeout)
            except requests.exceptions.Timeout as exc:
                if attempt >= max_attempts:
                    log.error(
                        "API request timed out (attempt %d/%d) url=%s",
                        attempt,
                        max_attempts,
                        url,
                    )
                    raise ScrapeError(
                        f"API request timed out after {max_attempts} attempts: {url}"
                    ) from exc
                delay = self._retry_delay(attempt)
                log.warning(
                    "API request timed out (attempt %d/%d) url=%s; retrying in %.1fs",
                    attempt,
                    max_attempts,
                    url,
                    delay,
                )
                time.sleep(delay)
                continue
            except requests.RequestException as exc:
                if attempt >= max_attempts:
                    log.error(
                        "API request network failure (attempt %d/%d) url=%s error=%s: %s",
                        attempt,
                        max_attempts,
                        url,
                        type(exc).__name__,
                        exc,
                    )
                    raise ScrapeError(
                        f"API request network failure after {max_attempts} attempts: "
                        f"{type(exc).__name__}: {url}"
                    ) from exc
                delay = self._retry_delay(attempt)
                log.warning(
                    "API request network failure (attempt %d/%d) url=%s error=%s: %s; "
                    "retrying in %.1fs",
                    attempt,
                    max_attempts,
                    url,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                time.sleep(delay)
                continue

            status = response.status_code
            body = self._body_summary(response)
            reason = response.reason or ""
            query_timeout = "timeout" in body.lower() or "slow down" in body.lower()

            if status == 422 and query_timeout:
                if attempt >= max_attempts:
                    log.error(
                        "API query timeout (attempt %d/%d) status=422 reason=%s body=%s "
                        "url=%s; no further retries",
                        attempt,
                        max_attempts,
                        reason,
                        body,
                        url,
                    )
                    raise QueryTimeoutError(
                        f"API query timeout after {max_attempts} attempts: "
                        f"status=422 reason={reason} body={body} url={url}"
                    )
                delay = config.QUERY_TIMEOUT_RETRY_DELAY
                log.warning(
                    "API query timeout (attempt %d/%d) status=422 reason=%s body=%s "
                    "url=%s; retrying in %.1fs",
                    attempt,
                    max_attempts,
                    reason,
                    body,
                    url,
                    delay,
                )
                time.sleep(delay)
                continue

            if status == 429:
                reset = response.headers.get("X-RateLimit-Reset", "<missing>")
                if attempt >= max_attempts:
                    log.error(
                        "API rate limited (attempt %d/%d) status=429 X-RateLimit-Reset=%s "
                        "body=%s url=%s; no further attempts",
                        attempt,
                        max_attempts,
                        reset,
                        body,
                        url,
                    )
                    raise ScrapeError(
                        f"API rate limited after {max_attempts} attempts: status=429 "
                        f"X-RateLimit-Reset={reset} body={body} url={url}"
                    )
                delay = self._rate_limit_delay(response)
                log.warning(
                    "API rate limited (attempt %d/%d) status=429 X-RateLimit-Reset=%s "
                    "body=%s url=%s; retrying in %.1fs",
                    attempt,
                    max_attempts,
                    reset,
                    body,
                    url,
                    delay,
                )
                time.sleep(delay)
                continue

            if status in {500, 502, 503, 504, 522, 523} or "query timed out" in body.lower():
                if attempt >= max_attempts:
                    log.error(
                        "API service failure (attempt %d/%d) status=%d reason=%s "
                        "body=%s url=%s; no further attempts",
                        attempt,
                        max_attempts,
                        status,
                        reason,
                        body,
                        url,
                    )
                    raise ScrapeError(
                        f"API service failure after {max_attempts} attempts: "
                        f"status={status} reason={reason} body={body} url={url}"
                    )
                delay = self._retry_delay(attempt)
                log.warning(
                    "API service failure (attempt %d/%d) status=%d reason=%s body=%s "
                    "url=%s; retrying in %.1fs",
                    attempt,
                    max_attempts,
                    status,
                    reason,
                    body,
                    url,
                    delay,
                )
                time.sleep(delay)
                continue

            if status >= 400:
                log.error(
                    "API request rejected status=%d reason=%s body=%s url=%s",
                    status,
                    reason,
                    body,
                    url,
                )
                raise ScrapeError(
                    f"API request rejected: status={status} reason={reason} body={body} url={url}"
                )

            try:
                data = response.json()
            except ValueError as exc:
                log.error(
                    "API returned invalid JSON status=%d body=%s url=%s",
                    status,
                    body,
                    url,
                )
                raise ScrapeError(
                    f"API returned invalid JSON: status={status} body={body} url={url}"
                ) from exc

            time.sleep(self.sleep_seconds)
            return data

        raise ScrapeError(f"Unexpected API request failure: {url}")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("redfetcher")


def _utc_datetime(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_api_datetime(value):
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def find_start_year(subreddit, client):
    current_year = datetime.now(timezone.utc).year
    for year in range(config.START_YEAR, current_year + 1):
        data = client.search_posts(
            subreddit=subreddit,
            after=f"{year}-01-01T00:00:00Z",
            before=f"{year + 1}-01-01T00:00:00Z",
            limit=1,
            sort="asc",
            fields=["id"],
        )
        posts = normalize_response(data)
        if posts:
            log.info("[%s] First posts found in %s", subreddit, year)
            return year
    return config.START_YEAR


def get_scrape_start(subreddit, client):
    checkpoint = database.get_scrape_state(subreddit)
    if checkpoint is not None:
        checkpoint_dt = datetime.fromtimestamp(checkpoint, timezone.utc)
        resume_dt = checkpoint_dt - timedelta(hours=config.RESUME_OVERLAP_HOURS)
        log.info(
            "[%s] Resuming from checkpoint %s with a %s-hour overlap",
            subreddit,
            checkpoint_dt.isoformat(),
            config.RESUME_OVERLAP_HOURS,
        )
        return resume_dt

    start_year = find_start_year(subreddit, client)
    log.info("[%s] No checkpoint found; starting a full backfill from %s", subreddit, start_year)
    return datetime(start_year, 1, 1, tzinfo=timezone.utc)


def _request_posts(client, subreddit, start, end, limit, fields, sort, stats):
    query_start = start - timedelta(seconds=1)
    query_end = end + timedelta(seconds=1)
    stats["requests"] += 1
    log.debug(
        "[%s] Requesting %s to %s (sort=%s)",
        subreddit,
        _format_api_datetime(query_start),
        _format_api_datetime(query_end),
        sort,
    )
    data = client.search_posts(
        subreddit=subreddit,
        after=_format_api_datetime(query_start),
        before=_format_api_datetime(query_end),
        limit=limit,
        sort=sort,
        fields=fields,
    )
    posts = normalize_response(data)
    stats["posts_returned"] += len(posts)
    return posts


def _merge_unique_posts(*groups):
    seen = set()
    merged = []
    for group in groups:
        for post in group:
            post_id = post.get("id")
            if post_id is not None:
                if post_id in seen:
                    continue
                seen.add(post_id)
            merged.append(post)
    return merged


def _fetch_window(
    client,
    subreddit,
    start,
    end,
    limit,
    fields,
    minimum_window,
    stats,
    on_complete,
):
    try:
        posts = _request_posts(client, subreddit, start, end, limit, fields, "asc", stats)
    except QueryTimeoutError as exc:
        if end - start <= minimum_window:
            raise QueryTimeoutError(
                f"[{subreddit}] Query timeout at minimum window width: "
                f"{_format_api_datetime(start)} to {_format_api_datetime(end)}: {exc}"
            ) from exc
        midpoint = start + (end - start) / 2
        log.warning(
            "[%s] Query timeout persisted; splitting window %s to %s",
            subreddit,
            _format_api_datetime(start),
            _format_api_datetime(end),
        )
        yield from _fetch_window(
            client,
            subreddit,
            start,
            midpoint,
            limit,
            fields,
            minimum_window,
            stats,
            on_complete,
        )
        yield from _fetch_window(
            client,
            subreddit,
            midpoint,
            end,
            limit,
            fields,
            minimum_window,
            stats,
            on_complete,
        )
        return
    except Exception as exc:
        raise ScrapeError(
            f"[{subreddit}] Request failed for {_format_api_datetime(start)} to "
            f"{_format_api_datetime(end)}: {exc}"
        ) from exc

    if len(posts) >= limit:
        if end - start <= minimum_window:
            desc_posts = _request_posts(
                client, subreddit, start, end, limit, fields, "desc", stats
            )
            merged = _merge_unique_posts(posts, desc_posts)
            log.warning(
                "[%s] Window saturated at minimum width (%s to %s): captured %d "
                "unique posts at the tie (asc=%d, desc=%d); any same-timestamp "
                "overflow beyond that cannot be fetched and is skipped",
                subreddit,
                _format_api_datetime(start),
                _format_api_datetime(end),
                len(merged),
                len(posts),
                len(desc_posts),
            )
            if merged:
                yield merged
            stats["completed_windows"] += 1
            if on_complete:
                on_complete(end)
            return
        midpoint = start + (end - start) / 2
        log.info(
            "[%s] Saturated window (%d posts); splitting %s to %s",
            subreddit,
            len(posts),
            _format_api_datetime(start),
            _format_api_datetime(end),
        )
        yield from _fetch_window(
            client,
            subreddit,
            start,
            midpoint,
            limit,
            fields,
            minimum_window,
            stats,
            on_complete,
        )
        yield from _fetch_window(
            client,
            subreddit,
            midpoint,
            end,
            limit,
            fields,
            minimum_window,
            stats,
            on_complete,
        )
        return

    if posts:
        yield posts
    stats["completed_windows"] += 1
    if on_complete:
        on_complete(end)
    if stats["completed_windows"] % 10 == 0:
        log.info(
            "[%s] Progress: %d complete windows, %d API requests, %d posts returned",
            subreddit,
            stats["completed_windows"],
            stats["requests"],
            stats["posts_returned"],
        )


def fetch_posts_batch(client, subreddit, start, end, limit=100, fields=None, on_complete=None):
    start = _utc_datetime(start)
    end = _utc_datetime(end)
    if start >= end:
        return
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")

    minimum_window = timedelta(seconds=config.MIN_SCRAPE_WINDOW_SECONDS)
    if minimum_window <= timedelta(0):
        raise ValueError("MIN_SCRAPE_WINDOW_SECONDS must be positive")

    stats = {"requests": 0, "completed_windows": 0, "posts_returned": 0}
    for window_start, window_end in split_time_range(start, end, step_hours=720):
        yield from _fetch_window(
            client,
            subreddit,
            window_start,
            window_end,
            limit,
            fields,
            minimum_window,
            stats,
            on_complete,
        )

    log.info(
        "[%s] Fetch complete: %d API requests, %d complete windows, %d posts returned",
        subreddit,
        stats["requests"],
        stats["completed_windows"],
        stats["posts_returned"],
    )


def scrape_subreddit(subreddit):
    client = LoggingArcticShiftClient(
        timeout=config.CLIENT_TIMEOUT,
        sleep_seconds=config.REQUEST_DELAY,
        max_retries=config.MAX_RETRIES,
        backoff_factor=config.BACKOFF_FACTOR,
    )
    end = datetime.now(timezone.utc)
    request_limit = min(config.POSTS_PER_REQUEST, 100)
    if request_limit < 1:
        raise ValueError("POSTS_PER_REQUEST must be between 1 and 100")

    start = get_scrape_start(subreddit, client)
    if start >= end:
        log.info("[%s] Checkpoint is already at the current time; nothing to scrape", subreddit)
        return 0

    log.info(
        "[%s] Scraping from %s to %s with a maximum of %d posts per request",
        subreddit,
        _format_api_datetime(start),
        _format_api_datetime(end),
        request_limit,
    )

    count = 0
    batch_num = 0

    def checkpoint(window_end):
        database.advance_scrape_state(subreddit, window_end.timestamp())
        log.debug("[%s] Checkpoint advanced to %s", subreddit, _format_api_datetime(window_end))

    try:
        for posts in fetch_posts_batch(
            client,
            subreddit=subreddit,
            start=start,
            end=end,
            limit=request_limit,
            fields=["id", "title", "url", "score", "num_comments", "created_utc", "subreddit"],
            on_complete=checkpoint,
        ):
            actual = database.insert_posts(posts)
            count += actual
            batch_num += 1
            if batch_num % 5 == 0:
                log.info("[%s] %d new posts inserted after %d batches", subreddit, count, batch_num)
    except KeyboardInterrupt:
        log.warning(
            "[%s] Interrupted after %d new posts and %d batches; rerun to resume",
            subreddit,
            count,
            batch_num,
        )
        raise
    except Exception as exc:
        log.error(
            "[%s] Scrape stopped at the last complete checkpoint after %d new posts: %s",
            subreddit,
            count,
            exc,
        )
        raise

    log.info("[%s] Done: %d new posts inserted in %d batches", subreddit, count, batch_num)
    return count


def scrape_all():
    total = 0
    failed = []
    log.info(
        "Starting scrape for %d subreddits: %s",
        len(config.SUBREDDITS),
        ", ".join(config.SUBREDDITS),
    )
    try:
        for subreddit in config.SUBREDDITS:
            try:
                total += scrape_subreddit(subreddit)
            except Exception as exc:
                log.error(
                    "[%s] Skipping subreddit after an unrecoverable error; "
                    "continuing with the rest: %s",
                    subreddit,
                    exc,
                )
                failed.append(subreddit)
    except KeyboardInterrupt:
        log.warning("Interrupted. Stopping scrape.")
        return 130
    if failed:
        log.warning(
            "Scrape finished with %d subreddit(s) skipped: %s",
            len(failed),
            ", ".join(failed),
        )
        return 1
    log.info("Scrape complete. %d new posts across %d subreddits.", total, len(config.SUBREDDITS))
    return 0


if __name__ == "__main__":
    database.init_db()
    log.info("Database initialized at %s", config.DB_PATH)

    try:
        if len(sys.argv) > 1:
            subreddit = sys.argv[1]
            if not SUBREDDIT_RE.match(subreddit):
                log.error(
                    "Invalid subreddit name: %r (must be 3-21 chars: letters, digits, underscores)",
                    subreddit,
                )
                sys.exit(1)
            scrape_subreddit(subreddit)
        else:
            sys.exit(scrape_all())
    except KeyboardInterrupt:
        log.info("Scrape stopped by user.")
        sys.exit(130)
    except Exception as exc:
        log.error("Scrape failed: %s", exc)
        sys.exit(1)
