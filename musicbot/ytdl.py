"""yt-dlp wrapper.

Every function here blocks (network I/O). Callers must run them with
asyncio.to_thread so the event loop keeps running.
"""

from __future__ import annotations

import logging

import yt_dlp
from yt_dlp.utils import DownloadError

from musicbot import config
# Module import (not `from ... import Track`) because player.py imports this module.
from musicbot import player

log = logging.getLogger(__name__)

BASE_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",  # force IPv4, avoids some failures
}

# Flat-playlist entries with these titles cannot be played.
UNAVAILABLE_TITLES = {"[Deleted video]", "[Private video]"}


class YTDLError(Exception):
    """A yt-dlp failure with a message that is safe to show to users."""


def _options(**extra) -> dict:
    opts = dict(BASE_OPTIONS, **extra)
    if config.COOKIES_FILE:
        opts["cookiefile"] = config.COOKIES_FILE
    return opts


def _friendly_message(error: Exception) -> str:
    text = str(error)
    lowered = text.lower()
    if "confirm you" in lowered and "not a bot" in lowered:
        return "YouTube wants a sign-in to confirm I'm not a bot (try updating yt-dlp or setting COOKIES_FILE)."
    if "private video" in lowered:
        return "this video is private."
    if "age" in lowered and ("restricted" in lowered or "confirm your age" in lowered):
        return "this video is age-restricted."
    if "in your country" in lowered:
        return "this video is blocked in this region."
    if "video unavailable" in lowered or "has been removed" in lowered:
        return "this video is unavailable."
    if "http error 403" in lowered:
        return "YouTube refused the request (403). Updating yt-dlp usually fixes this."
    return "couldn't load this video."


def _extract(url: str, **extra) -> dict:
    try:
        with yt_dlp.YoutubeDL(_options(**extra)) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as e:
        log.info("yt-dlp failed for %s: %s", url, e)
        raise YTDLError(_friendly_message(e)) from None
    if info is None:
        raise YTDLError("couldn't load this video.")
    return info


def _watch_url(entry: dict) -> str:
    video_id = entry.get("id")
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return entry.get("webpage_url") or entry["url"]


def _duration(entry: dict) -> int | None:
    value = entry.get("duration")
    return int(value) if value else None


def resolve_stream(page_url: str) -> tuple[str | None, int | None, str]:
    """Return (title, duration, stream_url) for a video page.

    Stream URLs expire after a few hours, so call this right before playback.
    """
    info = _extract(page_url)
    if "entries" in info:
        entries = [e for e in info["entries"] if e]
        if not entries:
            raise YTDLError("couldn't load this video.")
        info = entries[0]
    stream_url = info.get("url")
    if not stream_url:
        raise YTDLError("no playable audio stream was found.")
    return info.get("title"), _duration(info), stream_url


def search_or_resolve(query: str, requested_by: str) -> player.Track:
    """Turn a video URL or search text into a Track (without a stream URL)."""
    if query.startswith(("http://", "https://")):
        info = _extract(query)
        if "entries" in info:  # e.g. a URL yt-dlp treats as a list
            entries = [e for e in info["entries"] if e]
            if not entries:
                raise YTDLError("couldn't load this video.")
            info = entries[0]
        return player.Track(
            page_url=info.get("webpage_url") or _watch_url(info),
            title=info.get("title"),
            duration=_duration(info),
            requested_by=requested_by,
        )

    info = _extract(f"ytsearch1:{query}", extract_flat="in_playlist")
    entries = [e for e in info.get("entries") or [] if e]
    if not entries:
        raise YTDLError(f"no results for “{query}”.")
    entry = entries[0]
    return player.Track(
        page_url=_watch_url(entry),
        title=entry.get("title"),
        duration=_duration(entry),
        requested_by=requested_by,
    )


def expand_playlist(url: str, requested_by: str) -> list[player.Track]:
    """List the videos of a YouTube playlist. Fast: streams are resolved later."""
    info = _extract(url, noplaylist=False, extract_flat="in_playlist")
    tracks = []
    for entry in info.get("entries") or []:
        if not entry or entry.get("title") in UNAVAILABLE_TITLES:
            continue
        tracks.append(
            player.Track(
                page_url=_watch_url(entry),
                title=entry.get("title"),
                duration=_duration(entry),
                requested_by=requested_by,
            )
        )
    if not tracks:
        raise YTDLError("that playlist is empty or private.")
    return tracks
