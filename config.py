from pathlib import Path

SUBREDDITS = [
    "AIAssisted", "AIToolsPerformance", "ArtificialInteligence", "fossdroid", "LocalLLaMA", "LocalLLM", "LowEndLocalAI", "ObsidianMD", "opencode", "opencodeCLI", "OpenModels", "opensource", "PiCodingAgent", "Rainmeter", "selfhosted", "termux", "unixporn", "vibecoding", "web_design"
]

REQUEST_DELAY = 1.0
CLIENT_TIMEOUT = 15          # Reduced from 60
MAX_RETRIES = 2               # Reduced from 4
BACKOFF_FACTOR = 2.0

DB_PATH = str(Path(__file__).resolve().parent / "reddit_archive.db")

START_YEAR = 2005
POSTS_PER_REQUEST = 100
MIN_SCRAPE_WINDOW_HOURS = 1
QUERY_TIMEOUT_RETRY_DELAY = 20
RESUME_OVERLAP_HOURS = 48
