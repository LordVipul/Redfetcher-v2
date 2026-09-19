from urllib.parse import urlencode

import logging
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from datetime import datetime
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
    except (ValueError, OSError):
        return str(value)


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
# ── Shared context ───────────────────────────────────────────

def build_template_context(request: Request, extra=None):
    ctx = {
        "request": request,
        "bookmarked_ids": list(database.get_bookmark_ids()),
    }
    if extra:
        ctx.update(extra)
    return ctx


# ── Routes ───────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    stats = database.get_stats()
    return templates.TemplateResponse(
        request, "index.html",
        build_template_context(request, {"stats": stats})
    )


@app.get("/posts", response_class=HTMLResponse)
async def posts(
    request: Request,
    subreddit: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    sort: str = Query("oldest"),
    limit: int = Query(50),
    q: str = Query(None),
    page: int = Query(1, ge=1),
):
    results, total = database.get_posts(
        subreddit=subreddit, start_date=start, end_date=end,
        sort=sort, limit=limit, q=q, page=page,
    )
    all_subreddits = database.get_all_subreddits()
    total_pages = max(1, (total + limit - 1) // limit if limit > 0 else 1)
    log.info(
        f"GET /posts — subreddit={subreddit}, sort={sort}, limit={limit}, "
        f"page={page}, q={'yes' if q else 'no'}, results={len(results)}, total={total}"
    )
    return templates.TemplateResponse(
        request,
        "posts.html",
        build_template_context(request, {
            "posts": results,
            "filters": {"subreddit": subreddit, "start": start, "end": end, "sort": sort, "limit": limit, "q": q},
            "all_subreddits": all_subreddits,
            "total": total,
            "page": page,
            "total_pages": total_pages,
        }),
    )


@app.get("/api/posts")
async def api_posts(
    subreddit: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    sort: str = Query("oldest"),
    limit: int = Query(50),
    q: str = Query(None),
    page: int = Query(1, ge=1),
):
    results, total = database.get_posts(subreddit, start, end, sort, limit, q, page)
    log.info(f"GET /api/posts — {len(results)} posts returned")
    return {"posts": results, "total": total}


@app.get("/stats")
async def stats():
    data = database.get_stats()
    log.info(f"GET /stats — {len(data)} subreddits")
    return data


@app.get("/bookmarks", response_class=HTMLResponse)
async def bookmarks(
    request: Request,
    sort: str = Query("oldest"),
    limit: int = Query(50),
    page: int = Query(1, ge=1),
    subreddit: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    q: str = Query(None),
):
    results = database.get_bookmarks(
        limit=limit, offset=(page - 1) * limit, sort=sort,
        subreddit=subreddit, start_date=start, end_date=end, q=q,
    )
    total = database.get_bookmarks_count(
        subreddit=subreddit, start_date=start, end_date=end, q=q,
    )
    total_pages = max(1, (total + limit - 1) // limit if limit > 0 else 1)
    all_subreddits = database.get_all_subreddits()
    log.info(f"GET /bookmarks — subreddit={subreddit}, sort={sort}, q={'yes' if q else 'no'}, page={page}, total={total}")
    return templates.TemplateResponse(
        request,
        "bookmarks.html",
        build_template_context(request, {
            "posts": results,
            "filters": {"subreddit": subreddit, "sort": sort, "limit": limit, "start": start, "end": end, "q": q},
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "all_subreddits": all_subreddits,
        }),
    )


@app.get("/api/bookmarks")
async def api_bookmarks(
    subreddit: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    sort: str = Query("oldest"),
    limit: int = Query(50),
    q: str = Query(None),
    page: int = Query(1, ge=1),
):
    results = database.get_bookmarks(
        limit=limit, offset=(page - 1) * limit, sort=sort,
        subreddit=subreddit, start_date=start, end_date=end, q=q,
    )
    total = database.get_bookmarks_count(
        subreddit=subreddit, start_date=start, end_date=end, q=q,
    )
    log.info(f"GET /api/bookmarks — {len(results)} bookmarks")
    return {"posts": results, "total": total}


@app.post("/bookmarks/{post_id}")
async def add_bookmark(post_id: str):
    database.add_bookmark(post_id)
    log.info(f"POST /bookmarks/{post_id} — bookmark added")
    return JSONResponse({"status": "bookmarked", "post_id": post_id})


@app.delete("/bookmarks/{post_id}")
async def remove_bookmark(post_id: str):
    database.remove_bookmark(post_id)
    log.info(f"DELETE /bookmarks/{post_id} — bookmark removed")
    return JSONResponse({"status": "unbookmarked", "post_id": post_id})


# ── Entry point ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    database.init_db()
    log.info("Starting server on http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)
