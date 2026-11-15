from musicbot.commands import queue_entry
from musicbot.player import Track


def test_queue_entry_with_metadata():
    track = Track(page_url="https://www.youtube.com/watch?v=a", title="Song *bold*", duration=185, requested_by="x")
    assert queue_entry(track) == "Song \\*bold\\* [3:05]"


def test_queue_entry_without_title_suppresses_link_preview():
    track = Track(page_url="https://www.youtube.com/watch?v=a", title=None, duration=None, requested_by="x")
    assert queue_entry(track) == "<https://www.youtube.com/watch?v=a>"
    track.lookup = "failed"
    assert queue_entry(track) == "<https://www.youtube.com/watch?v=a> (couldn't load info)"
