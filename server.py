from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime
import config
import database

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
    return database.get_posts(subreddit, start, end, sort, limit, q)


@app.get("/stats")
async def stats():
    return database.get_stats()


if __name__ == "__main__":
    import uvicorn
    database.init_db()
    uvicorn.run(app, host="127.0.0.1", port=8000)
