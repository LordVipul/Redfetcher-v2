"""Maintenance jobs that run once per server startup.

Two curation rules that automation can decide without ever touching
judgement-based content quality:

1. hide_duplicated_posts — same subreddit + identical title (length >=
   DUP_TITLE_MIN_CHARS) with every copy inside DUP_WINDOW_SECONDS keeps only
   the earliest post. Near-simultaneous same-title floods are bot campaigns;
   months-apart repeats of the same title are legitimately distinct posts and
   are left visible.

2. check_external_links — HEAD-checks a capped batch of never-checked (or
   stale) external URLs. 404/410 or a DNS failure means the post can't be
   read and is hidden; anything inconclusive (timeouts, 5xx, 403) is recorded
   for a later recheck instead of being hidden. Reddit permalinks are never
   checked: reddit serves 200 even for deleted posts.

Both jobs are idempotent and converge: already-hidden posts and already-
checked rows are skipped, so repeated startups re-check nothing.
"""

import logging
import socket
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests

import config
import database

log = logging.getLogger("redfetcher.maintenance")

# Realistic browser UA: plenty of sites 403 the default python-requests UA.
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/128.0"}


def hide_duplicated_posts():
    """Hide non-earliest copies of same-day same-title posts. Returns count."""
    conn = database.get_connection()
    try:
        rows = conn.execute(
            f"""
            WITH first_copies AS (
                SELECT subreddit, title
                FROM posts
                WHERE hidden = 'false' AND title IS NOT NULL
                  AND LENGTH(title) > ?
                GROUP BY subreddit, title
                HAVING COUNT(*) > 1 AND (MAX(created_utc) - MIN(created_utc)) < ?
            ),
            dupes AS (
                SELECT p.id, ROW_NUMBER() OVER (
                    PARTITION BY p.subreddit, p.title
                    ORDER BY p.created_utc ASC, p.id ASC
                ) AS rn
                FROM posts p
                JOIN first_copies f ON f.subreddit = p.subreddit AND f.title = p.title
                WHERE p.hidden = 'false'
            )
            SELECT id FROM dupes WHERE rn > 1
            """,
            (config.DUP_TITLE_MIN_CHARS, config.DUP_WINDOW_SECONDS),
        ).fetchall()
        ids = [r["id"] for r in rows]
        hidden = 0
        for start in range(0, len(ids), 5000):
            chunk = ids[start : start + 5000]
            placeholders = ",".join("?" * len(chunk))
            hidden += conn.execute(
                f"UPDATE posts SET hidden = 'true' WHERE id IN ({placeholders}) AND hidden = 'false'",
                chunk,
            ).rowcount
        conn.commit()
        return hidden
    finally:
        conn.close()


def _get_urls_to_check():
    """External, visible, never-checked (or stale) URLs, capped per run.

    `*reddit.com/*` permalinks are excluded — reddit returns 200 for deleted
    posts, so a check can't distinguish readable from removed content.
    Reddit-hosted media (`i.redd.it`, `v.redd.it`) still rots and is checked.
    """
    conn = database.get_connection()
    try:
        return [
            r["url"]
            for r in conn.execute(
                """
                SELECT p.url
                FROM posts p
                LEFT JOIN link_checks c ON c.url = p.url
                WHERE p.hidden = 'false'
                  AND p.url LIKE 'http%'
                  AND p.url NOT LIKE '%reddit.com/%'
                  AND (
                    c.url IS NULL
                    OR (c.status NOT IN ('dead', 'dns_fail')
                        AND c.checked_at < datetime('now', ?))
                  )
                LIMIT ?
                """,
                (f"-{config.DEAD_LINK_RECHECK_DAYS} days", config.DEAD_LINK_CHECK_LIMIT),
            ).fetchall()
        ]
    finally:
        conn.close()


def _check_url(url):
    """Return a status word for one URL: ok / dead / dns_fail / error.

    Only conclusive results hide the post — a distant server having a bad
    second is not evidence the content is gone.
    """
    host = urllib.parse.urlparse(url).hostname
    if not host:
        return "error"
    timeout = config.DEAD_LINK_TIMEOUT
    try:
        resp = requests.head(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        status = resp.status_code
    except requests.RequestException:
        # Timeouts/5xx-adjacent failures are inconclusive, but a hostname the
        # OS cannot resolve at all IS conclusive: the domain is gone.
        return _dns_verdict(host)
    # HEAD-unfriendly servers: retry once with GET before trusting the status.
    if status in (405, 501):
        try:
            resp = requests.get(
                url, headers=HEADERS, timeout=timeout, allow_redirects=True, stream=True
            )
            resp.close()
            status = resp.status_code
        except requests.RequestException:
            return "error"
    return "dead" if status in (404, 410) else "ok"


def _dns_verdict(host):
    try:
        socket.getaddrinfo(host, None)
        return "error"
    except socket.gaierror:
        return "dns_fail"


def check_external_links():
    """Check a capped batch of external URLs; hide conclusive deaths.

    Results (including 'ok' and retries) are cached in link_checks so each
    URL is visited at most once per DEAD_LINK_RECHECK_DAYS.
    """
    urls = _get_urls_to_check()
    if not urls:
        return {"checked": 0}
    statuses = {}
    with ThreadPoolExecutor(max_workers=config.DEAD_LINK_CONCURRENCY) as pool:
        for url, status in zip(urls, pool.map(_check_url, urls)):
            statuses[url] = status
    dead = [u for u, s in statuses.items() if s in ("dead", "dns_fail")]
    conn = database.get_connection()
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO link_checks (url, status, checked_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP)",
            list(statuses.items()),
        )
        conn.commit()
    finally:
        conn.close()
    hidden = database.hide_posts_by_urls(dead)
    return {
        "checked": len(statuses),
        "dead": len([u for u, s in statuses.items() if s == "dead"]),
        "dns_fail": len([u for u, s in statuses.items() if s == "dns_fail"]),
        "hidden": hidden,
    }


def run_maintenance():
    """Both curation jobs, safe to run at every startup. Never raises."""
    try:
        dups = hide_duplicated_posts()
        log.info("Maintenance: hid %d duplicate posts", dups)
    except Exception:
        log.exception("Maintenance: duplicate hiding failed")
    try:
        summary = check_external_links()
        if summary.get("checked"):
            log.info(
                "Maintenance: checked %d links — %d dead, %d dns-fail, %d hidden",
                summary["checked"], summary.get("dead", 0),
                summary.get("dns_fail", 0), summary.get("hidden", 0),
            )
    except Exception:
        log.exception("Maintenance: dead-link check failed")
