from urllib.parse import urlencode

import asyncio
import logging
import re
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from datetime import datetime, timedelta, timezone
import config
import database

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("redfetcher")

app = FastAPI(title="RedFetcher v2")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="templates/static"), name="static")


def format_date(value):
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OverflowError, OSError):
        return str(value)


def to_timestamp(date_str):
    """Convert an ISO date string to a Unix timestamp, or None."""
    if not date_str:
        return None
    return datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc).timestamp()


def to_end_timestamp(date_str):
    if not date_str:
        return None
    parsed = datetime.fromisoformat(date_str)
    if len(date_str.strip()) == 10:
        parsed += timedelta(days=1)
    return parsed.replace(tzinfo=timezone.utc).timestamp()


# ── Input validation ─────────────────────────────────────────

SUBREDDIT_RE = re.compile(r"^[a-zA-Z0-9_]{3,21}$")
POST_SORTS = {"score_desc", "score_asc", "comments_desc", "comments_asc", "newest", "oldest"}
BOOKMARK_SORTS = {"oldest", "newest", "score_desc", "score_asc", "comments_desc", "comments_asc"}
Q_MIN_LEN, Q_MAX_LEN = 2, 100


def validate_filters(subreddit=None, start=None, end=None, sort=None, q=None, allowed_sorts=None):
    """Validate query parameters. Returns an error message, or None if valid."""
    if subreddit and not SUBREDDIT_RE.match(subreddit):
        return f"invalid subreddit name: {subreddit!r}"
    if sort is not None and allowed_sorts is not None and sort not in allowed_sorts:
        return f"invalid sort: {sort!r} (allowed: {', '.join(sorted(allowed_sorts))})"
    if q not in (None, "") and not (Q_MIN_LEN <= len(q) <= Q_MAX_LEN):
        return f"q must be between {Q_MIN_LEN} and {Q_MAX_LEN} characters"
    try:
        start_ts = to_timestamp(start)
        end_ts = to_end_timestamp(end)
    except (ValueError, OverflowError):
        return "invalid date: start and end must be ISO format (e.g. 2024-01-31)"
    if start_ts is not None and end_ts is not None and start_ts >= end_ts:
        return "invalid date range: start must be before end"
    return None


def bad_request(message):
    return JSONResponse(status_code=400, content={"error": message})


templates.env.filters["datetimeformat"] = format_date


def query_params(page, filters=None):
    """Build a query string preserving current filters with a new page number."""
    params = {}
    if filters:
        for k, v in filters.items():
            if v is not None and v != "":
                params[k] = str(v)
    params["page"] = str(page)
    return urlencode(params)


templates.env.globals["query_params"] = query_params


def redirect_to_page(path, page, filters):
    return RedirectResponse(url=f"{path}?{query_params(page, filters)}", status_code=302)


templates.env.globals["abs"] = abs
# ── Shared context ───────────────────────────────────────────

async def build_template_context(request: Request, extra=None, post_ids=(), bookmarked_ids=None):
    if bookmarked_ids is None:
        bookmarked_ids = (
            await asyncio.to_thread(database.get_bookmark_ids_for, list(post_ids))
            if post_ids
            else []
        )
    ctx = {
        "request": request,
        "bookmarked_ids": list(bookmarked_ids),
    }
    if extra:
        ctx.update(extra)
    return ctx


# ── Routes ───────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    stats = await asyncio.to_thread(database.get_stats)
    return templates.TemplateResponse(
        request, "index.html",
        await build_template_context(request, {"stats": stats})
    )


@app.get("/posts", response_class=HTMLResponse)
async def posts(
    request: Request,
    subreddit: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    sort: str = Query("oldest"),
    limit: int = Query(50, ge=1, le=500),
    q: str = Query(None),
    page: int = Query(1, ge=1),
):
    if err := validate_filters(subreddit, start, end, sort, q, POST_SORTS):
        return bad_request(err)
    results, total = await asyncio.to_thread(
        database.get_posts,
        subreddit=subreddit, start_date=to_timestamp(start), end_date=to_end_timestamp(end),
        sort=sort, limit=limit, q=q, page=page,
    )
    all_subreddits = await asyncio.to_thread(database.get_all_subreddits)
    total_pages = max(1, (total + limit - 1) // limit if limit > 0 else 1)
    if page > total_pages:
        return redirect_to_page(
            "/posts",
            total_pages,
            {"subreddit": subreddit, "start": start, "end": end, "sort": sort, "limit": limit, "q": q},
        )
    log.info(
        f"GET /posts — subreddit={subreddit}, sort={sort}, limit={limit}, "
        f"page={page}, q={'yes' if q else 'no'}, results={len(results)}, total={total}"
    )
    return templates.TemplateResponse(
        request,
        "posts.html",
        await build_template_context(request, {
            "posts": results,
            "filters": {"subreddit": subreddit, "start": start, "end": end, "sort": sort, "limit": limit, "q": q},
            "all_subreddits": all_subreddits,
            "total": total,
            "page": page,
            "total_pages": total_pages,
        }, post_ids=[p["id"] for p in results]),
    )


@app.get("/api/posts")
async def api_posts(
    subreddit: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    sort: str = Query("oldest"),
    limit: int = Query(50, ge=1, le=500),
    q: str = Query(None),
    page: int = Query(1, ge=1),
):
    if err := validate_filters(subreddit, start, end, sort, q, POST_SORTS):
        return bad_request(err)
    results, total = await asyncio.to_thread(
        database.get_posts,
        subreddit=subreddit, start_date=to_timestamp(start), end_date=to_end_timestamp(end),
        sort=sort, limit=limit, q=q, page=page,
    )
    total_pages = max(1, (total + limit - 1) // limit if limit > 0 else 1)
    if page > total_pages:
        page = total_pages
        if total > 0:
            results, total = await asyncio.to_thread(
                database.get_posts,
                subreddit=subreddit, start_date=to_timestamp(start), end_date=to_end_timestamp(end),
                sort=sort, limit=limit, q=q, page=page,
            )
    log.info(f"GET /api/posts — {len(results)} posts returned")
    bookmarked_ids = await asyncio.to_thread(
        database.get_bookmark_ids_for, [p["id"] for p in results]
    )
    return {
        "posts": results,
        "total": total,
        "bookmarked_ids": bookmarked_ids,
        "page": page,
        "total_pages": total_pages,
    }


@app.get("/stats")
async def stats():
    data = await asyncio.to_thread(database.get_stats)
    log.info(f"GET /stats — {len(data)} subreddits")
    return data


@app.get("/bookmarks", response_class=HTMLResponse)
async def bookmarks(
    request: Request,
    sort: str = Query("oldest"),
    limit: int = Query(50, ge=1, le=500),
    page: int = Query(1, ge=1),
    subreddit: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    q: str = Query(None),
):
    if err := validate_filters(subreddit, start, end, sort, q, BOOKMARK_SORTS):
        return bad_request(err)
    results, total = await asyncio.to_thread(
        database.get_bookmarks,
        limit=limit, offset=(page - 1) * limit, sort=sort,
        subreddit=subreddit, start_date=to_timestamp(start), end_date=to_end_timestamp(end), q=q,
    )
    total_pages = max(1, (total + limit - 1) // limit if limit > 0 else 1)
    if page > total_pages:
        return redirect_to_page(
            "/bookmarks",
            total_pages,
            {"subreddit": subreddit, "sort": sort, "limit": limit, "start": start, "end": end, "q": q},
        )
    all_subreddits = await asyncio.to_thread(database.get_all_subreddits)
    log.info(f"GET /bookmarks — subreddit={subreddit}, sort={sort}, q={'yes' if q else 'no'}, page={page}, total={total}")
    return templates.TemplateResponse(
        request,
        "bookmarks.html",
        await build_template_context(request, {
            "posts": results,
            "filters": {"subreddit": subreddit, "sort": sort, "limit": limit, "start": start, "end": end, "q": q},
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "all_subreddits": all_subreddits,
        }, bookmarked_ids=[p["id"] for p in results]),
    )


@app.get("/api/bookmarks")
async def api_bookmarks(
    subreddit: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    sort: str = Query("oldest"),
    limit: int = Query(50, ge=1, le=500),
    q: str = Query(None),
    page: int = Query(1, ge=1),
):
    if err := validate_filters(subreddit, start, end, sort, q, BOOKMARK_SORTS):
        return bad_request(err)
    results, total = await asyncio.to_thread(
        database.get_bookmarks,
        limit=limit, offset=(page - 1) * limit, sort=sort,
        subreddit=subreddit, start_date=to_timestamp(start), end_date=to_end_timestamp(end), q=q,
    )
    total_pages = max(1, (total + limit - 1) // limit if limit > 0 else 1)
    if page > total_pages:
        page = total_pages
        if total > 0:
            results, total = await asyncio.to_thread(
                database.get_bookmarks,
                limit=limit, offset=(page - 1) * limit, sort=sort,
                subreddit=subreddit, start_date=to_timestamp(start), end_date=to_end_timestamp(end), q=q,
            )
    log.info(f"GET /api/bookmarks — {len(results)} bookmarks")
    return {
        "posts": results,
        "total": total,
        "bookmarked_ids": [p["id"] for p in results],
        "page": page,
        "total_pages": total_pages,
    }


@app.post("/bookmarks/{post_id}")
async def add_bookmark(post_id: str):
    await asyncio.to_thread(database.add_bookmark, post_id)
    log.info(f"POST /bookmarks/{post_id} — bookmark added")
    return JSONResponse({"status": "bookmarked", "post_id": post_id})


@app.delete("/bookmarks/{post_id}")
async def remove_bookmark(post_id: str):
    await asyncio.to_thread(database.remove_bookmark, post_id)
    log.info(f"DELETE /bookmarks/{post_id} — bookmark removed")
    return JSONResponse({"status": "unbookmarked", "post_id": post_id})


# ── Entry point ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    database.init_db()
    log.info("Starting server on http://127.0.0.1:8000")
    # Local-only tool: never expose this server publicly — no auth is implemented.
    uvicorn.run(app, host="127.0.0.1", port=8000)
