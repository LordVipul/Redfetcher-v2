import logging
import time
import sys
from datetime import datetime
from arcshiftwrap.arctic_shift import ArcticShiftClient, split_time_range, normalize_response

import config
import database

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("redfetcher")


# ── Helpers ──────────────────────────────────────────────────────────

def find_start_year(subreddit, client):
    """Find the earliest year that has posts for this subreddit."""
    for year in range(config.START_YEAR, datetime.now().year + 1):
        data = client.search_posts(
            subreddit=subreddit,
            after=f"{year}-01-01",
            before=f"{year + 1}-01-01",
            limit=1,
            sort="asc",
            fields=["id"],
        )
        posts = normalize_response(data)
        if posts:
            log.info(f"[{subreddit}] First posts found in {year}")
            return year
    return config.START_YEAR


def get_scrape_start(subreddit, client):
    """Return the date to start scraping from.

    If the DB has stored posts, resume from the most recent one.
    Otherwise, find the earliest year with posts via yearly scan.
    """
    max_utc = database.get_max_created_utc(subreddit)
    if max_utc:
        dt = datetime.fromtimestamp(max_utc)
        log.info(f"[{subreddit}] Resuming from {dt.date()} (last stored)")
        return dt
    start_year = find_start_year(subreddit, client)
    log.info(f"[{subreddit}] No prior data. Starting from {start_year}")
    return datetime(start_year, 1, 1)


def fetch_posts_batch(client, subreddit, start, end, limit=100, fields=None):
    """Fetch all posts between start and end using monthly windows.
    When the current month is reached, switches to daily windows to
    ensure all posts are captured (monthly windows are capped at 100 posts).
    """
    last_window_start = None
    last_window_end = None
    last_had_limit = False

    for window_start, window_end in split_time_range(start, end, step_hours=720):
        last_window_start = window_start
        last_window_end = window_end
        try:
            data = client.search_posts(
                subreddit=subreddit,
                after=window_start.strftime("%Y-%m-%d"),
                before=window_end.strftime("%Y-%m-%d"),
                limit=limit,
                sort="asc",
                fields=fields,
            )
            posts = normalize_response(data)
        except Exception as e:
            log.warning(f"[{subreddit}] Error fetching {window_start.date()}: {e}")
            continue

        if not posts:
            continue

        yield posts
        if len(posts) >= limit:
            last_had_limit = True

    # If the last window was the current month (truncated to 'end') and hit the limit,
    # switch to daily windows to capture remaining posts
    if last_had_limit and last_window_end and last_window_end.date() >= end.date():
        for window_start, window_end in split_time_range(last_window_start, end, step_hours=24):
            try:
                data = client.search_posts(
                    subreddit=subreddit,
                    after=window_start.strftime("%Y-%m-%d"),
                    before=window_end.strftime("%Y-%m-%d"),
                    limit=limit,
                    sort="asc",
                    fields=fields,
                )
                posts = normalize_response(data)
            except Exception as e:
                log.warning(f"[{subreddit}] Error fetching {window_start.date()}: {e}")
                continue

            if not posts:
                continue

            yield posts


# ── Scrape ───────────────────────────────────────────────────────────

def scrape_subreddit(subreddit):
    client = ArcticShiftClient(
        timeout=config.CLIENT_TIMEOUT,
        max_retries=config.MAX_RETRIES,
        backoff_factor=config.BACKOFF_FACTOR,
    )
    end = datetime.now()
    start = get_scrape_start(subreddit, client)

    log.info(f"[{subreddit}] Scraping r/{subreddit} from {start.date()} to {end.date()}")

    count = 0
    batch_num = 0
    try:
        for posts in fetch_posts_batch(
            client,
            subreddit=subreddit,
            start=start,
            end=end,
            limit=config.POSTS_PER_REQUEST,
            fields=["id", "title", "url", "score", "num_comments", "created_utc", "subreddit"],
        ):
            actual = database.insert_posts(posts)
            count += actual
            batch_num += 1

            if batch_num % 5 == 0:
                log.info(f"[{subreddit}] {count} posts scraped so far…")

            time.sleep(config.REQUEST_DELAY)
    except KeyboardInterrupt:
        log.warning(f"[{subreddit}] Interrupted! {count} new posts inserted.")
        log.info(f"[{subreddit}] Resume with: python scraper.py {subreddit}")
        raise
    except Exception as e:
        log.error(f"[{subreddit}] Unexpected error: {e}", exc_info=True)
        return count

    log.info(f"[{subreddit}] Done. {count} new posts inserted in {batch_num} batches.")
    return count


def scrape_all():
    total = 0
    log.info(f"Starting scrape for {len(config.SUBREDDITS)} subreddits: {', '.join(config.SUBREDDITS)}")
    try:
        for subreddit in config.SUBREDDITS:
            try:
                count = scrape_subreddit(subreddit)
                total += count
            except KeyboardInterrupt:
                log.warning("Interrupted. Stopping scrape.")
                break
            except Exception as e:
                log.error(f"[{subreddit}] Fatal error: {e}", exc_info=True)
    except KeyboardInterrupt:
        pass
    log.info(f"Scrape complete. {total} new posts across {len(config.SUBREDDITS)} subreddits.")


if __name__ == "__main__":
    database.init_db()
    log.info("Database initialized.")

    try:
        if len(sys.argv) > 1:
            subreddit = sys.argv[1]
            scrape_subreddit(subreddit)
        else:
            scrape_all()
    except KeyboardInterrupt:
        log.info("\nScrape stopped by user.")
