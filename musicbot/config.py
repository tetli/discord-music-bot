"""Configuration: loads .env and holds constants."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
COOKIES_FILE = os.getenv("COOKIES_FILE", "").strip() or None
# DEBUG_VOICE=1 turns on voice/playback debug logging and a per-track health check.
DEBUG_VOICE = os.getenv("DEBUG_VOICE", "").strip().lower() in ("1", "true", "yes")

PLAYLISTS_DIR = PROJECT_ROOT / "playlists"
MAX_PLAYLIST_ENTRIES = 500

DEFAULT_VOLUME = 0.5
IDLE_TIMEOUT = 5 * 60  # seconds with nothing playing before leaving
ALONE_TIMEOUT = 2 * 60  # seconds alone in the voice channel before leaving
QUEUE_PAGE_SIZE = 10
METADATA_CONCURRENCY = 3  # parallel title/duration lookups for the queue view
QUEUE_METADATA_TIMEOUT = 8  # seconds /queue waits for missing titles before showing the page

FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTIONS = "-vn"
