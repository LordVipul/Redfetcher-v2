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
app.mount("/fonts", StaticFiles(directory="fonts"), name="fonts")


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
VIEWS = {"feed", "bookmarks"}
Q_MIN_LEN, Q_MAX_LEN = 2, 100
MAX_MIN_FILTER = 1_000_000_000


def parse_min_filter(value):
    """Normalize a minimum score/comments param.

    None or "" means "no filter". "0" is a real filter meaning >= 0, which is
    distinct from no filter because score and num_comments can be negative.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
    parsed = int(value)
    if parsed < 0:
        raise ValueError("must be 0 or greater")
    if parsed > MAX_MIN_FILTER:
        raise ValueError(f"must be at most {MAX_MIN_FILTER}")
    return parsed


def validate_filters(subreddit=None, start=None, end=None, sort=None, q=None, allowed_sorts=None, min_score=None, min_comments=None):
    """Validate query parameters. Returns an error message, or None if valid."""
    if subreddit and not SUBREDDIT_RE.match(subreddit):
        return f"invalid subreddit name: {subreddit!r}"
    if sort is not None and allowed_sorts is not None and sort not in allowed_sorts:
        return f"invalid sort: {sort!r} (allowed: {', '.join(sorted(allowed_sorts))})"
    if q not in (None, "") and not (Q_MIN_LEN <= len(q) <= Q_MAX_LEN):
        return f"q must be between {Q_MIN_LEN} and {Q_MAX_LEN} characters"
    for name, raw in (("min_score", min_score), ("min_comments", min_comments)):
        try:
            parse_min_filter(raw)
        except (TypeError, ValueError):
            return f"invalid {name}: must be a whole number, 0 or greater"
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
            if v is None or v == "":
                continue
            if k == "group" and v == "all":
                continue
            if k == "view" and v != "bookmarks":
                continue
            params[k] = str(v)
    params["page"] = str(page)
    return urlencode(params)


templates.env.globals["query_params"] = query_params


def redirect_to_page(path, page, filters):
    return RedirectResponse(url=f"{path}?{query_params(page, filters)}", status_code=302)


templates.env.globals["abs"] = abs


# ── Groups ───────────────────────────────────────────────────

def resolve_group(key):
    """Return (label, subreddits or None) for a group key; None if unknown.

    The special key "all" means no subreddit restriction.
    """
    if key in (None, "", "all"):
        return "All", None
    if key not in config.GROUPS:
        return None
    label, subs = config.GROUPS[key]
    return label, list(subs)


def build_sidebar(active_key, view):
    """Sidebar model: All + every group with post counts, plus the Bookmarks row."""
    stats = database.get_stats()
    counts = {s["subreddit"]: s["count"] for s in stats}
    total = sum(counts.values())
    items = [{"key": "all", "label": "All", "count": total, "active": view == "feed" and active_key == "all"}]
    groups = [
        {
            "key": key,
            "label": label,
            "count": sum(counts.get(s, 0) for s in subs),
            "active": view == "feed" and active_key == key,
        }
        for key, (label, subs) in config.GROUPS.items()
    ]
    groups.sort(key=lambda g: g["label"].casefold())
    items.extend(groups)
    return items


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
async def index(
    request: Request,
    group: str = Query("all"),
    view: str = Query("feed"),
    subreddit: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    sort: str = Query("oldest"),
    limit: int = Query(50, ge=1, le=500),
    q: str = Query(None),
    page: int = Query(1, ge=1),
    min_score: str = Query(None),
    min_comments: str = Query(None),
):
    if view not in VIEWS:
        return bad_request(f"invalid view: {view!r} (allowed: feed, bookmarks)")
    resolved = resolve_group(group)
    if resolved is None:
        return bad_request(f"invalid group: {group!r}")

    is_bookmarks = view == "bookmarks"
    allowed_sorts = BOOKMARK_SORTS if is_bookmarks else POST_SORTS
    if err := validate_filters(subreddit, start, end, sort, q, allowed_sorts, min_score, min_comments):
        return bad_request(err)

    # Bookmarks are always ordered oldest-first by when they were saved.
    effective_sort = "oldest" if is_bookmarks else sort
    min_score, min_comments = parse_min_filter(min_score), parse_min_filter(min_comments)
    filters = {
        "group": group, "view": view if is_bookmarks else "feed",
        "subreddit": subreddit, "start": start, "end": end,
        "sort": effective_sort, "limit": limit, "q": q,
        "min_score": min_score, "min_comments": min_comments,
    }

    group_label, group_subs = resolved
    if is_bookmarks:
        results, total = await asyncio.to_thread(
            database.get_bookmarks,
            limit=limit, offset=(page - 1) * limit, sort=effective_sort,
            subreddit=subreddit, start_date=to_timestamp(start), end_date=to_end_timestamp(end), q=q,
            min_score=min_score, min_comments=min_comments,
        )
    else:
        results, total = await asyncio.to_thread(
            database.get_posts,
            subreddit=subreddit, subreddits=group_subs,
            start_date=to_timestamp(start), end_date=to_end_timestamp(end),
            sort=effective_sort, limit=limit, q=q, page=page,
            min_score=min_score, min_comments=min_comments,
        )

    total_pages = max(1, (total + limit - 1) // limit if limit > 0 else 1)
    if page > total_pages:
        return redirect_to_page("/", total_pages, filters)

    sidebar = await asyncio.to_thread(build_sidebar, group, view)
    bookmark_count = await asyncio.to_thread(database.get_bookmark_count)
    if is_bookmarks:
        all_subreddits = await asyncio.to_thread(database.get_all_subreddits)
    else:
        all_subreddits = group_subs if group_subs is not None else await asyncio.to_thread(
            database.get_all_subreddits
        )

    log.info(
        f"GET / — view={view}, group={group}, sort={effective_sort}, limit={limit}, "
        f"page={page}, q={'yes' if q else 'no'}, results={len(results)}, total={total}"
    )
    return templates.TemplateResponse(
        request,
        "feed.html",
        await build_template_context(request, {
            "posts": results,
            "filters": filters,
            "all_subreddits": all_subreddits,
            "sidebar": sidebar,
            "bookmark_count": bookmark_count,
            "view": view,
            "group": group,
            "group_label": group_label,
            "is_bookmarks": is_bookmarks,
            "total": total,
            "page": page,
            "total_pages": total_pages,
        }, post_ids=[p["id"] for p in results]),
    )


@app.get("/api/posts")
async def api_posts(
    group: str = Query("all"),
    subreddit: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    sort: str = Query("oldest"),
    limit: int = Query(50, ge=1, le=500),
    q: str = Query(None),
    page: int = Query(1, ge=1),
    min_score: str = Query(None),
    min_comments: str = Query(None),
):
    if err := validate_filters(subreddit, start, end, sort, q, POST_SORTS, min_score, min_comments):
        return bad_request(err)
    resolved = resolve_group(group)
    if resolved is None:
        return bad_request(f"invalid group: {group!r}")
    _, group_subs = resolved
    min_score = parse_min_filter(min_score)
    min_comments = parse_min_filter(min_comments)
    results, total = await asyncio.to_thread(
        database.get_posts,
        subreddit=subreddit, subreddits=group_subs,
        start_date=to_timestamp(start), end_date=to_end_timestamp(end),
        sort=sort, limit=limit, q=q, page=page,
        min_score=min_score, min_comments=min_comments,
    )
    total_pages = max(1, (total + limit - 1) // limit if limit > 0 else 1)
    if page > total_pages:
        page = total_pages
        if total > 0:
            results, total = await asyncio.to_thread(
                database.get_posts,
                subreddit=subreddit, subreddits=group_subs,
                start_date=to_timestamp(start), end_date=to_end_timestamp(end),
                sort=sort, limit=limit, q=q, page=page,
                min_score=min_score, min_comments=min_comments,
            )
    log.info(f"GET /api/posts — group={group}, {len(results)} posts returned")
    bookmarked_ids = await asyncio.to_thread(
        database.get_bookmark_ids_for, [p["id"] for p in results]
    )
    if group_subs is not None:
        group_subreddits = group_subs
    else:
        group_subreddits = await asyncio.to_thread(database.get_all_subreddits)
    return {
        "posts": results,
        "total": total,
        "bookmarked_ids": bookmarked_ids,
        "page": page,
        "total_pages": total_pages,
        "subreddits": group_subreddits,
    }


@app.get("/stats")
async def stats():
    data = await asyncio.to_thread(database.get_stats)
    log.info(f"GET /stats — {len(data)} subreddits")
    return data


@app.get("/api/bookmarks")
async def api_bookmarks(
    subreddit: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    sort: str = Query("oldest"),
    limit: int = Query(50, ge=1, le=500),
    q: str = Query(None),
    page: int = Query(1, ge=1),
    min_score: str = Query(None),
    min_comments: str = Query(None),
):
    if err := validate_filters(subreddit, start, end, sort, q, BOOKMARK_SORTS, min_score, min_comments):
        return bad_request(err)
    min_score = parse_min_filter(min_score)
    min_comments = parse_min_filter(min_comments)
    results, total = await asyncio.to_thread(
        database.get_bookmarks,
        limit=limit, offset=(page - 1) * limit, sort=sort,
        subreddit=subreddit, start_date=to_timestamp(start), end_date=to_end_timestamp(end), q=q,
        min_score=min_score, min_comments=min_comments,
    )
    total_pages = max(1, (total + limit - 1) // limit if limit > 0 else 1)
    if page > total_pages:
        page = total_pages
        if total > 0:
            results, total = await asyncio.to_thread(
                database.get_bookmarks,
                limit=limit, offset=(page - 1) * limit, sort=sort,
                subreddit=subreddit, start_date=to_timestamp(start), end_date=to_end_timestamp(end), q=q,
                min_score=min_score, min_comments=min_comments,
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
    added = await asyncio.to_thread(database.add_bookmark, post_id)
    if not added:
        log.info("POST /bookmarks/%s — post not found", post_id)
        return JSONResponse(status_code=404, content={"error": "post not found"})
    log.info(f"POST /bookmarks/{post_id} — bookmark added")
    return JSONResponse({"status": "bookmarked", "post_id": post_id})


@app.delete("/bookmarks/{post_id}")
async def remove_bookmark(post_id: str):
    await asyncio.to_thread(database.remove_bookmark, post_id)
    log.info(f"DELETE /bookmarks/{post_id} — bookmark removed")
    return JSONResponse({"status": "unbookmarked", "post_id": post_id})


# ── Dashboard ─────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    data = await asyncio.to_thread(database.get_dashboard_data)
    counts = {s["subreddit"]: s["count"] for s in data["per_subreddit"]}
    group_totals = [
        {
            "key": key,
            "label": label,
            "count": sum(counts.get(s, 0) for s in subs),
            "sub_count": len(subs),
        }
        for key, (label, subs) in config.GROUPS.items()
    ]
    log.info(
        f"GET /dashboard — {data['totals']['posts']} posts, "
        f"{data['totals']['subreddits']} subreddits"
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        await build_template_context(request, {
            "totals": data["totals"],
            "per_subreddit": data["per_subreddit"],
            "monthly": data["monthly"],
            "top_posts": data["top_posts"],
            "group_totals": group_totals,
        }),
    )


# ── Legacy route redirects ───────────────────────────────────

@app.get("/posts")
async def posts_redirect(request: Request):
    return RedirectResponse(url=f"/?{request.url.query}", status_code=302)


@app.get("/bookmarks")
async def bookmarks_redirect(request: Request):
    qs = request.url.query
    url = "/?view=bookmarks" + (f"&{qs}" if qs else "")
    return RedirectResponse(url=url, status_code=302)


# ── Entry point ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    database.init_db()
    log.info("Starting server on http://127.0.0.1:8000")
    # Local-only tool: never expose this server publicly — no auth is implemented.
    uvicorn.run(app, host="127.0.0.1", port=8000)
