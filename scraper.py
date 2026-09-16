import logging
import time
import sys
from datetime import datetime
from arcshiftwrap.arctic_shift import ArcticShiftClient, split_time_range, normalize_response

import config
import database

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("redfetcher")


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_resume_point(subreddit):
    max_utc = database.get_max_created_utc(subreddit)
    if max_utc:
        dt = datetime.fromtimestamp(max_utc)
        log.info(f"[{subreddit}] Resuming from {dt.isoformat()} (last stored)")
        return dt
    start = datetime(config.START_YEAR, 1, 1)
    log.info(f"[{subreddit}] No prior data. Starting from {start.date()}")
    return start


def scrape_posts_generator(client, subreddit, start, end, step_hours=720, limit=100, fields=None):
    """Yield posts one at a time from the Arctic Shift API, using monthly windows."""
    for window_start, window_end in split_time_range(start, end, step_hours):
        log.debug(f"[{subreddit}] Fetching {window_start.date()} → {window_end.date()}")
        data = client.search_posts(
            subreddit=subreddit,
            after=window_start.strftime("%Y-%m-%d"),
            before=window_end.strftime("%Y-%m-%d"),
            limit=limit,
            sort="asc",
            fields=fields,
        )
        posts = normalize_response(data)
        for post in posts:
            yield post


# ── Scrape ───────────────────────────────────────────────────────────────────

def scrape_subreddit(subreddit):
    client = ArcticShiftClient(
        timeout=config.CLIENT_TIMEOUT,
        max_retries=config.MAX_RETRIES,
        backoff_factor=config.BACKOFF_FACTOR,
    )
    start = get_resume_point(subreddit)
    end = datetime.now()

    log.info(f"[{subreddit}] Scraping r/{subreddit} from {start.date()} to {end.date()}")

    count = 0
    batch = 0
    try:
        for post in scrape_posts_generator(
            client,
            subreddit=subreddit,
            start=start,
            end=end,
            step_hours=720,
            limit=config.POSTS_PER_REQUEST,
            fields=["id", "title", "url", "score", "num_comments", "created_utc", "subreddit"],
        ):
            database.insert_post(post)
            count += 1
            batch += 1

            if batch % 50 == 0:
                log.info(f"[{subreddit}] {count} posts scraped so far…")

            time.sleep(config.REQUEST_DELAY)
    except KeyboardInterrupt:
        log.warning(f"[{subreddit}] Interrupted! {count} new posts inserted.")
        log.info(f"[{subreddit}] Resume with: python scraper.py {subreddit}")
        return count
    except Exception as e:
        log.error(f"[{subreddit}] Unexpected error: {e}", exc_info=True)
        return count

    log.info(f"[{subreddit}] Done. {count} new posts inserted.")
    return count


def scrape_all():
    total = 0
    log.info(f"Starting scrape for {len(config.SUBREDDITS)} subreddits: {', '.join(config.SUBREDDITS)}")
    for subreddit in config.SUBREDDITS:
        try:
            count = scrape_subreddit(subreddit)
            total += count
        except Exception as e:
            log.error(f"[{subreddit}] Fatal error: {e}", exc_info=True)
    log.info(f"Scrape complete. {total} new posts across {len(config.SUBREDDITS)} subreddits.")


if __name__ == "__main__":
    database.init_db()
    log.info("Database initialized.")

    if len(sys.argv) > 1:
        subreddit = sys.argv[1]
        scrape_subreddit(subreddit)
    else:
        scrape_all()
