"""Reading and validating .txt playlist files.

Format: one YouTube URL per line. Blank lines and lines starting with # are
ignored. Lines that aren't YouTube URLs are skipped and reported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from musicbot import config

NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
YOUTUBE_HOSTS = {"youtube.com", "youtu.be"}


class PlaylistError(Exception):
    """A problem with a playlist name or file, safe to show to users."""


@dataclass
class ParseResult:
    urls: list[str] = field(default_factory=list)
    skipped_lines: list[int] = field(default_factory=list)  # 1-based line numbers
    capped: bool = False  # True if entries beyond the cap were dropped


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for prefix in ("www.", "m.", "music."):
        if host.startswith(prefix):
            return host[len(prefix) :]
    return host


def is_youtube_url(text: str) -> bool:
    if not text.startswith(("http://", "https://")):
        return False
    return _host(text) in YOUTUBE_HOSTS


def is_playlist_url(url: str) -> bool:
    """A youtube.com/playlist?list=... URL (not a watch URL that happens to have list=)."""
    if not is_youtube_url(url) or _host(url) != "youtube.com":
        return False
    parsed = urlparse(url)
    return parsed.path.rstrip("/") == "/playlist" and bool(parse_qs(parsed.query).get("list"))


def parse_playlist_text(text: str, max_entries: int = config.MAX_PLAYLIST_ENTRIES) -> ParseResult:
    result = ParseResult()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not is_youtube_url(line):
            result.skipped_lines.append(number)
            continue
        if len(result.urls) >= max_entries:
            result.capped = True
            continue
        result.urls.append(line)
    return result


def playlist_path(name: str, base: Path = config.PLAYLISTS_DIR) -> Path:
    """Path of playlists/<name>.txt. Rejects names that could escape the folder."""
    if not NAME_PATTERN.match(name):
        raise PlaylistError("Playlist names may only contain letters, numbers, `-` and `_`.")
    base = base.resolve()
    path = (base / f"{name}.txt").resolve()
    if not path.is_relative_to(base) or path.parent != base:
        raise PlaylistError("Invalid playlist name.")
    return path


def read_playlist(name: str, base: Path = config.PLAYLISTS_DIR, max_entries: int = config.MAX_PLAYLIST_ENTRIES) -> ParseResult:
    """Blocking: call with asyncio.to_thread."""
    path = playlist_path(name, base)
    if not path.is_file():
        raise PlaylistError(f"There is no playlist called `{name}`. Use /playlist list to see them.")
    # utf-8-sig also handles files saved by Notepad with a byte order mark.
    return parse_playlist_text(path.read_text(encoding="utf-8-sig", errors="replace"), max_entries)


def playlist_names(base: Path = config.PLAYLISTS_DIR) -> list[str]:
    if not base.is_dir():
        return []
    return sorted(p.stem for p in base.glob("*.txt") if p.is_file() and NAME_PATTERN.match(p.stem))


def list_playlists(base: Path = config.PLAYLISTS_DIR) -> list[tuple[str, int]]:
    """(name, entry count) for every playlist. Blocking: call with asyncio.to_thread."""
    return [(name, len(read_playlist(name, base, max_entries=10**9).urls)) for name in playlist_names(base)]


def add_to_playlist(name: str, url: str, base: Path = config.PLAYLISTS_DIR) -> bool:
    """Append url as a new line, creating the file if needed.

    Returns True if the file was created. Blocking: call with asyncio.to_thread.
    """
    url = url.strip()
    if not is_youtube_url(url) or any(c.isspace() for c in url):
        raise PlaylistError("That isn't a YouTube link (it must start with http:// or https:// and point at youtube.com or youtu.be).")
    path = playlist_path(name, base)
    created = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if not created:
        existing = path.read_bytes()
        if existing and not existing.endswith(b"\n"):
            prefix = "\n"
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(f"{prefix}{url}\n")
    return created
