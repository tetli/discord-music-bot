"""yt-dlp is mocked: these tests never touch the network."""

import pytest
from yt_dlp.utils import DownloadError

from musicbot import ytdl


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL. Set FakeYDL.result / FakeYDL.error per test."""

    result = None
    error = None
    calls: list = []

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download):
        assert download is False
        FakeYDL.calls.append((url, self.options))
        if FakeYDL.error:
            raise FakeYDL.error
        return FakeYDL.result


@pytest.fixture(autouse=True)
def fake_ydl(monkeypatch):
    FakeYDL.result, FakeYDL.error, FakeYDL.calls = None, None, []
    monkeypatch.setattr(ytdl.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(ytdl.config, "COOKIES_FILE", None)
    return FakeYDL


def test_resolve_stream():
    FakeYDL.result = {"title": "Song", "duration": 212.0, "url": "https://stream"}
    assert ytdl.resolve_stream("https://youtu.be/x") == ("Song", 212, "https://stream")
    url, options = FakeYDL.calls[0]
    assert options["format"] == "bestaudio/best"
    assert options["noplaylist"] is True
    assert "cookiefile" not in options


def test_cookies_file_is_passed(monkeypatch):
    monkeypatch.setattr(ytdl.config, "COOKIES_FILE", "cookies.txt")
    FakeYDL.result = {"title": "Song", "url": "https://stream"}
    ytdl.resolve_stream("https://youtu.be/x")
    assert FakeYDL.calls[0][1]["cookiefile"] == "cookies.txt"


@pytest.mark.parametrize(
    "message, expected",
    [
        ("ERROR: [youtube] x: Video unavailable", "unavailable"),
        ("ERROR: [youtube] x: Private video. Sign in", "private"),
        ("ERROR: Sign in to confirm you’re not a bot", "not a bot"),
        ("ERROR: Sign in to confirm your age. This video may be inappropriate", "age-restricted"),
        ("ERROR: The uploader has not made this video available in your country", "region"),
        ("ERROR: something odd", "couldn't load"),
    ],
)
def test_download_errors_become_friendly(message, expected):
    FakeYDL.error = DownloadError(message)
    with pytest.raises(ytdl.YTDLError) as info:
        ytdl.resolve_stream("https://youtu.be/x")
    assert expected in str(info.value)


def test_search_uses_top_result():
    FakeYDL.result = {"entries": [{"id": "abc123", "title": "Top hit", "duration": 100}]}
    track = ytdl.search_or_resolve("some song", "alice")
    assert FakeYDL.calls[0][0] == "ytsearch1:some song"
    assert track.page_url == "https://www.youtube.com/watch?v=abc123"
    assert track.title == "Top hit"
    assert track.requested_by == "alice"


def test_search_without_results():
    FakeYDL.result = {"entries": []}
    with pytest.raises(ytdl.YTDLError):
        ytdl.search_or_resolve("nothing matches", "alice")


def test_resolve_url():
    FakeYDL.result = {"id": "abc", "title": "Vid", "duration": 30, "webpage_url": "https://www.youtube.com/watch?v=abc"}
    track = ytdl.search_or_resolve("https://youtu.be/abc", "bob")
    assert track.page_url == "https://www.youtube.com/watch?v=abc"
    assert track.duration == 30


def test_expand_playlist_skips_unavailable():
    FakeYDL.result = {
        "entries": [
            {"id": "a", "title": "One", "duration": 10},
            {"id": "b", "title": "[Deleted video]"},
            None,
            {"id": "c", "title": "Three"},
        ]
    }
    tracks = ytdl.expand_playlist("https://www.youtube.com/playlist?list=PL1", "carol")
    assert [t.title for t in tracks] == ["One", "Three"]
    assert tracks[1].duration is None
    options = FakeYDL.calls[0][1]
    assert options["noplaylist"] is False
    assert options["extract_flat"] == "in_playlist"


def test_expand_empty_playlist():
    FakeYDL.result = {"entries": []}
    with pytest.raises(ytdl.YTDLError):
        ytdl.expand_playlist("https://www.youtube.com/playlist?list=PL1", "carol")
