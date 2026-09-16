import logging
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime
import config
import database

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("redfetcher")

app = FastAPI(title="RedFetcher v2")
templates = Jinja2Templates(directory="templates")


def format_date(value):
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError):
        return str(value)


templates.env.filters["datetimeformat"] = format_date


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    stats = database.get_stats()
    log.info(f"GET / — {len(stats)} subreddits served")
    return templates.TemplateResponse(request, "index.html", {"stats": stats})


@app.get("/posts", response_class=HTMLResponse)
async def posts(
    request: Request,
    subreddit: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    sort: str = Query("score_desc"),
    limit: int = Query(100),
    q: str = Query(None),
):
    results = database.get_posts(
        subreddit=subreddit, start_date=start, end_date=end,
        sort=sort, limit=limit, q=q,
    )
    all_subreddits = database.get_all_subreddits()
    log.info(
        f"GET /posts — subreddit={subreddit}, sort={sort}, limit={limit}, "
        f"q={'yes' if q else 'no'}, results={len(results)}"
    )
    return templates.TemplateResponse(
        request,
        "posts.html",
        {
            "posts": results,
            "filters": {"subreddit": subreddit, "start": start, "end": end, "sort": sort, "limit": limit, "q": q},
            "all_subreddits": all_subreddits,
            "total": len(results),
        },
    )


@app.get("/api/posts")
async def api_posts(
    subreddit: str = Query(None),
    start: str = Query(None),
    end: str = Query(None),
    sort: str = Query("score_desc"),
    limit: int = Query(100),
    q: str = Query(None),
):
    results = database.get_posts(subreddit, start, end, sort, limit, q)
    log.info(f"GET /api/posts — {len(results)} posts returned")
    return results


@app.get("/stats")
async def stats():
    data = database.get_stats()
    log.info(f"GET /stats — {len(data)} subreddits")
    return data


if __name__ == "__main__":
    import uvicorn
    database.init_db()
    log.info("Starting server on http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)
