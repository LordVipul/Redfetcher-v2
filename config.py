SUBREDDITS = [
    "AIAssisted", "AIToolsPerformance", "ArtificialInteligence", "fossdroid", "LocalLLaMA", "LocalLLM", "LowEndLocalAI", "ObsidianMD", "opencode", "opencodeCLI", "OpenModels", "opensource", "PiCodingAgent", "Rainmeter", "selfhosted", "termux", "unixporn", "vibecoding", "web_design"
]

API_BASE = "https://arctic-shift.photon-reddit.com/api"
REQUEST_DELAY = 0.35
CLIENT_TIMEOUT = 15          # Reduced from 60
MAX_RETRIES = 2               # Reduced from 4
BACKOFF_FACTOR = 2.0

DB_PATH = "reddit_archive.db"

START_YEAR = 2005
POSTS_PER_REQUEST = 100
SORT_ORDER = "asc"
