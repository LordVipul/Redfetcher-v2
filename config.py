from pathlib import Path

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
        "accessibility", "design_critiques", "DesignPorn", "graphic_design", "Typography", "UI_Design", "userexperience", "web_design",
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

REQUEST_DELAY = 1.0
CLIENT_TIMEOUT = 30          # Long enough for Cloudflare 52x outage pages to surface instead of masking as client timeouts
MAX_ATTEMPTS = 3              # Total attempts per request (1 initial + retries); formerly MAX_RETRIES = 2
BACKOFF_FACTOR = 2.0

DB_PATH = str(Path(__file__).resolve().parent / "reddit_archive.db")

START_YEAR = 2005
POSTS_PER_REQUEST = 100
MIN_SCRAPE_WINDOW_SECONDS = 1
QUERY_TIMEOUT_BACKOFF = (20, 40)  # Escalating waits (s) for query-timeout 422 retries; last value repeats
RESUME_OVERLAP_HOURS = 48
