import pytest

from musicbot import playlists
from musicbot.playlists import PlaylistError, parse_playlist_text


def test_comments_blanks_and_whitespace():
    text = """# Chill mix
https://www.youtube.com/watch?v=aaaaaaaaaaa

   https://youtu.be/bbbbbbbbbbb
\t# indented comment
"""
    result = parse_playlist_text(text)
    assert result.urls == ["https://www.youtube.com/watch?v=aaaaaaaaaaa", "https://youtu.be/bbbbbbbbbbb"]
    assert result.skipped_lines == []
    assert not result.capped


def test_invalid_lines_are_skipped_with_line_numbers():
    text = "\n".join(
        [
            "https://www.youtube.com/watch?v=ok1",  # 1
            "not a url",  # 2
            "www.youtube.com/watch?v=noscheme",  # 3
            "https://vimeo.com/123",  # 4
            "ftp://youtube.com/watch?v=x",  # 5
            "https://evil.com/?youtube.com",  # 6
            "https://youtube.com.evil.com/watch?v=x",  # 7
            "https://music.youtube.com/watch?v=ok2",  # 8
            "http://m.youtube.com/watch?v=ok3",  # 9
        ]
    )
    result = parse_playlist_text(text)
    assert result.skipped_lines == [2, 3, 4, 5, 6, 7]
    assert len(result.urls) == 3


def test_playlist_urls_are_kept_as_entries():
    text = "https://www.youtube.com/playlist?list=PL123\n"
    assert parse_playlist_text(text).urls == ["https://www.youtube.com/playlist?list=PL123"]


def test_cap():
    text = "\n".join(f"https://youtu.be/{i:011d}" for i in range(10))
    result = parse_playlist_text(text, max_entries=4)
    assert len(result.urls) == 4
    assert result.capped
    exact = parse_playlist_text(text, max_entries=10)
    assert len(exact.urls) == 10
    assert not exact.capped


def test_empty_file():
    result = parse_playlist_text("")
    assert result.urls == [] and result.skipped_lines == []


def test_is_playlist_url():
    assert playlists.is_playlist_url("https://www.youtube.com/playlist?list=PL123")
    assert playlists.is_playlist_url("https://music.youtube.com/playlist?list=PL123")
    assert not playlists.is_playlist_url("https://www.youtube.com/watch?v=abc&list=PL123")
    assert not playlists.is_playlist_url("https://www.youtube.com/playlist")
    assert not playlists.is_playlist_url("https://youtu.be/abc")
    assert not playlists.is_playlist_url("https://example.com/playlist?list=PL123")


@pytest.mark.parametrize(
    "name",
    ["../secret", "../../etc/passwd", "a/b", "a\\b", "", ".", "..", "name.txt", "with space", "/abs", "ümlaut", "a\nb"],
)
def test_bad_names_are_rejected(tmp_path, name):
    with pytest.raises(PlaylistError):
        playlists.playlist_path(name, tmp_path)


def test_good_name_stays_inside_folder(tmp_path):
    path = playlists.playlist_path("Chill_mix-2", tmp_path)
    assert path == (tmp_path / "Chill_mix-2.txt").resolve()


def test_read_playlist_from_disk(tmp_path):
    # Written with a BOM and CRLF, like Windows Notepad might.
    (tmp_path / "mix.txt").write_bytes(
        "﻿# comment\r\nhttps://youtu.be/aaaaaaaaaaa\r\nbad line\r\n".encode("utf-8")
    )
    result = playlists.read_playlist("mix", tmp_path)
    assert result.urls == ["https://youtu.be/aaaaaaaaaaa"]
    assert result.skipped_lines == [3]


def test_read_missing_playlist(tmp_path):
    with pytest.raises(PlaylistError):
        playlists.read_playlist("nope", tmp_path)


def test_list_playlists(tmp_path):
    (tmp_path / "b.txt").write_text("https://youtu.be/1\nhttps://youtu.be/2\n")
    (tmp_path / "a.txt").write_text("# only a comment\n")
    (tmp_path / "bad name.txt").write_text("https://youtu.be/1\n")
    (tmp_path / "notes.md").write_text("ignored")
    assert playlists.list_playlists(tmp_path) == [("a", 0), ("b", 2)]


def test_add_to_playlist(tmp_path):
    assert playlists.add_to_playlist("new", "https://youtu.be/aaaaaaaaaaa", tmp_path) is True
    (tmp_path / "old.txt").write_text("https://youtu.be/1")  # no trailing newline
    assert playlists.add_to_playlist("old", " https://youtu.be/2 ", tmp_path) is False
    assert (tmp_path / "new.txt").read_text() == "https://youtu.be/aaaaaaaaaaa\n"
    assert (tmp_path / "old.txt").read_text() == "https://youtu.be/1\nhttps://youtu.be/2\n"


def test_add_rejects_bad_input(tmp_path):
    with pytest.raises(PlaylistError):
        playlists.add_to_playlist("x", "https://vimeo.com/1", tmp_path)
    with pytest.raises(PlaylistError):
        playlists.add_to_playlist("../x", "https://youtu.be/1", tmp_path)
    with pytest.raises(PlaylistError):
        playlists.add_to_playlist("x", "https://youtu.be/1\nhttps://youtu.be/2", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_example_playlist_is_valid():
    result = playlists.read_playlist("example")
    assert result.urls and not result.skipped_lines
