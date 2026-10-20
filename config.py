from pathlib import Path
import os

# Groups drive both the scraper (via SUBREDDITS below) and the web UI sidebar.
# Each entry: key -> (short label, subreddits). The sidebar sorts groups by
# label alphabetically; "All" stays first and Bookmarks last either way.
GROUPS = {
    "ai": ("A.I.", [
        "AIAssisted", "AIToolsPerformance", "ArtificialInteligence", "learnmachinelearning", "LocalLLaMA", "LocalLLM", "LowEndLocalAI", "MachineLearning", "opencode", "opencodeCLI", "PiCodingAgent", "vibecoding",
    ]),
    "web": ("Web dev", [
        "css", "Frontend", "javascript", "js", "learnjavascript", "node", "webdev",
    ]),
    "design": ("Design", [
        "accessibility", "design_critiques", "DesignPorn", "graphic_design", "typography", "UI_Design", "userexperience", "web_design",
    ]),
    "cs": ("CS", [
        "algorithms", "AskProgramming", "codegolf", "coding", "compsci", "computerscience", "DailyProgrammer", "learnprogramming", "leetcode", "osdev", "programming",
    ]),
    "tools": ("Tools", [
        "AutoHotkey", "git", "neovim", "Rainmeter", "vim", "vscode", "zsh",
    ]),
    "databases": ("Databases", [
        "Database", "SQL", "sqlite",
    ]),
    "selfhosted": ("Self-host", [
        "DataHoarder", "firefox", "FOSS", "homeassistant", "homelab", "linux", "linuxquestions", "opensource", "selfhosted", "sysadmin",
    ]),
    "customisation": ("Customisation", [
        "FirefoxCSS", "hyprland", "neofetch", "Polybar", "terminal_porn", "unixporn", "wallpaperengine", "wallpapers",
    ]),
    "android": ("Android", [
        "Android", "androidapps", "androidthemes", "fossdroid", "homescreen", "kustom", "termux",
    ]),
    "games": ("Games", [
        "AndroidGaming", "gaming", "RimWorld", "Stellaris",
    ]),
    "setups": ("Setups", [
        "AverageBattlestations", "battlestations", "DecentBattlestations", "desksetup", "goodbattlestations", "HomeOffice", "retrobattlestations",
    ]),
    "organisation": ("Organisation", [
        "Declutter", "DeskCableManagement", "minimalism", "organization", "organizationporn",
    ]),
    "scifi": ("Sci-fi", [
        "astrophysics", "printSF", "scifi", "space",
    ]),
    "maker": ("Maker", [
        "arduino", "electronics", "raspberry_pi", "RightToRepair",
    ]),
    "personal": ("Personal", [
        "DigitalEscapeTools", "drawing", "ObsidianMD", "origami", "productivity", "simpleliving",
    ]),
}

SUBREDDITS = [sub for _, subs in GROUPS.values() for sub in subs]

REQUEST_DELAY = 2.0
CLIENT_TIMEOUT = 30          # Long enough for Cloudflare 52x outage pages to surface instead of masking as client timeouts
MAX_ATTEMPTS = 3              # Total attempts per request (1 initial + retries); formerly MAX_RETRIES = 2
BACKOFF_FACTOR = 2.0

# Database path. RF_DB_PATH lets a second instance (e.g. a dev copy under
# _dev/) run against a different file without touching the real archive.
DB_PATH = os.environ.get("RF_DB_PATH") or str(Path(__file__).resolve().parent / "reddit_archive.db")

START_YEAR = 2005
POSTS_PER_REQUEST = 100
MIN_SCRAPE_WINDOW_SECONDS = 1
QUERY_TIMEOUT_BACKOFF = (20, 40)  # Escalating waits (s) for query-timeout 422 retries; last value repeats
TIMEOUT_COOLDOWN_BASE = 2.0   # Extra sleep after a request that recovered a query timeout; doubles per consecutive timeout
TIMEOUT_COOLDOWN_CAP = 30.0   # Upper bound for the adaptive query-timeout cooldown
RESUME_OVERLAP_HOURS = 48

# Post curation (maintenance.py): the duplicate rule hides same-subreddit
# posts with an identical title longer than this, when every copy landed
# inside this window; the earliest copy is always kept.
DUP_TITLE_MIN_CHARS = 30
DUP_WINDOW_SECONDS = 7 * 86400

# Dead-link checker: an external URL that answers 404/410 or fails DNS is
# hidden automatically; anything less conclusive (timeouts, 5xx, 403) gets
# marked for a recheck after DEAD_LINK_RECHECK_DAYS instead. Reddit permalinks
# are never checked — reddit serves 200 even for deleted posts, so checking
# them can never distinguish readable from removed content.
DEAD_LINK_CHECK_LIMIT = 2000      # max URLs checked per maintenance run
DEAD_LINK_CONCURRENCY = 8         # parallel HTTP requests
DEAD_LINK_TIMEOUT = 15            # seconds per request
DEAD_LINK_RECHECK_DAYS = 7        # recheck inconclusive statuses after this
