from pathlib import Path

SUBREDDITS = [
    # ── AI, ML & AI-assisted coding ──
    "AIAssisted", "AIToolsPerformance", "ArtificialInteligence", "learnmachinelearning", "LocalLLaMA", "LocalLLM", "LowEndLocalAI", "MachineLearning", "opencode", "opencodeCLI", "PiCodingAgent", "vibecoding",

    # ── Frontend & web development ──
    "css", "Frontend", "javascript", "js", "learnjavascript", "node", "webdev",

    # ── Design, UX & typography ──
    "accessibility", "design_critiques", "DesignPorn", "graphic_design", "Typography", "UI_Design", "userexperience", "web_design",

    # ── Programming — general & practice ──
    "algorithms", "AskProgramming", "codegolf", "coding", "DailyProgrammer", "learnprogramming", "leetcode", "programming",

    # ── Computer science ──
    "compsci", "computerscience", "osdev",

    # ── Developer tooling ──
    "git", "neovim", "vim", "vscode", "zsh",

    # ── Databases ──
    "Database", "SQL", "sqlite",

    # ── Self-hosting, Linux & privacy ──
    "DataHoarder", "firefox", "FOSS", "homeassistant", "homelab", "linux", "linuxquestions", "opensource", "selfhosted", "sysadmin",

    # ── Linux ricing ──
    "hyprland", "neofetch", "Polybar", "terminal_porn", "unixporn",

    # ── Windows customisation ──
    "AutoHotkey", "Rainmeter",

    # ── Cross-platform theming ──
    "FirefoxCSS", "wallpaperengine", "wallpapers",

    # ── Android customisation ──
    "Android", "androidapps", "androidthemes", "homescreen", "kustom",

    # ── Android FOSS & tools ──
    "fossdroid", "termux",

    # ── Battlestations & setups ──
    "AverageBattlestations", "battlestations", "DecentBattlestations", "desksetup", "goodbattlestations", "HomeOffice", "retrobattlestations",

    # ── Workspace organisation & decluttering ──
    "Declutter", "DeskCableManagement", "minimalism", "organization", "organizationporn",

    # ── Science & fiction ──
    "astrophysics", "printSF", "scifi", "space",

    # ── Maker & right-to-repair ──
    "arduino", "electronics", "raspberry_pi", "RightToRepair",

    # ── Productivity & personal ──
    "drawing", "ObsidianMD", "origami", "productivity", "simpleliving",
]

REQUEST_DELAY = 1.0
CLIENT_TIMEOUT = 30          # Long enough for Cloudflare 52x outage pages to surface instead of masking as client timeouts
MAX_RETRIES = 2               # Reduced from 4
BACKOFF_FACTOR = 2.0

DB_PATH = str(Path(__file__).resolve().parent / "reddit_archive.db")

START_YEAR = 2005
POSTS_PER_REQUEST = 100
MIN_SCRAPE_WINDOW_HOURS = 1
QUERY_TIMEOUT_RETRY_DELAY = 20
RESUME_OVERLAP_HOURS = 48
