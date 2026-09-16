import time
import sys
from datetime import datetime
from arcshiftwrap.arctic_shift import ArcticShiftClient, split_time_range, normalize_response

import config
import database


def get_resume_point(subreddit):
    max_utc = database.get_max_created_utc(subreddit)
    if max_utc:
        return datetime.fromtimestamp(max_utc)
    return datetime(config.START_YEAR, 1, 1)


def scrape_posts_generator(client, subreddit, start, end, step_hours=720, limit=100, fields=None):
    """Yield posts one at a time from the Arctic Shift API, using monthly time windows.

    Daily windows (step_hours=24) are too narrow for sparse subreddits — the API's
    after/before date filtering returns nothing when a window has no posts.
    Monthly windows (720h) ensure we capture posts in sparse subreddits.
    """
    for window_start, window_end in split_time_range(start, end, step_hours):
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


def scrape_subreddit(subreddit):
    client = ArcticShiftClient(
        timeout=config.CLIENT_TIMEOUT,
        max_retries=config.MAX_RETRIES,
        backoff_factor=config.BACKOFF_FACTOR,
    )
    start = get_resume_point(subreddit)
    end = datetime.now()

    print(f"[{subreddit}] Starting scrape from {start.date()} to {end.date()}")
    print(f"[{subreddit}] Use Ctrl+C to pause gracefully — progress is saved incrementally.")

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
                print(f"[{subreddit}] {count} posts so far...")

            time.sleep(config.REQUEST_DELAY)
    except KeyboardInterrupt:
        print(f"\n{'='*50}")
        print(f"[{subreddit}] Interrupted! {count} new posts inserted before exit.")
        print(f"[{subreddit}] Resume later with: python scraper.py {subreddit}")
        print(f"{'='*50}")
        return count

    print(f"[{subreddit}] Done. {count} new posts inserted.")
    return count


def scrape_all():
    total = 0
    for subreddit in config.SUBREDDITS:
        try:
            count = scrape_subreddit(subreddit)
            total += count
        except Exception as e:
            print(f"[{subreddit}] ERROR: {e}")
    print(f"\nTotal: {total} new posts across {len(config.SUBREDDITS)} subreddits.")


if __name__ == "__main__":
    database.init_db()

    if len(sys.argv) > 1:
        subreddit = sys.argv[1]
        scrape_subreddit(subreddit)
    else:
        scrape_all()
