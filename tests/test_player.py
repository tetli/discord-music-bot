import asyncio

import pytest

from musicbot import ytdl
from musicbot.player import GuildPlayer, Track, TrackQueue, format_duration


def make_track(name: str) -> Track:
    return Track(page_url=f"https://www.youtube.com/watch?v={name}", title=name, duration=60, requested_by="tester")


def names(tracks) -> list[str]:
    return [t.title for t in tracks]


# ---- TrackQueue -----------------------------------------------------------


def test_add_returns_position_of_first_track():
    q = TrackQueue()
    assert q.add([make_track("a"), make_track("b")]) == 1
    assert q.add([make_track("c")]) == 3


def test_advance_loop_off_plays_in_order_then_stops():
    q = TrackQueue()
    q.add([make_track("a"), make_track("b")])
    assert q.advance().title == "a"
    assert q.advance().title == "b"
    assert q.advance() is None
    assert q.current is None


def test_loop_track_repeats_current():
    q = TrackQueue()
    q.loop_mode = "track"
    q.add([make_track("a"), make_track("b")])
    assert q.advance().title == "a"
    assert q.advance().title == "a"
    assert q.advance().title == "a"
    assert names(q.upcoming) == ["b"]


def test_loop_track_skip_moves_on():
    q = TrackQueue()
    q.loop_mode = "track"
    q.add([make_track("a"), make_track("b")])
    q.advance()
    assert q.advance(skipped=True).title == "b"
    assert q.advance().title == "b"


def test_loop_queue_reappends_finished_tracks():
    q = TrackQueue()
    q.loop_mode = "queue"
    q.add([make_track("a"), make_track("b"), make_track("c")])
    played = [q.advance().title for _ in range(7)]
    assert played == ["a", "b", "c", "a", "b", "c", "a"]


def test_loop_queue_single_track_repeats():
    q = TrackQueue()
    q.loop_mode = "queue"
    q.add([make_track("a")])
    assert [q.advance().title for _ in range(3)] == ["a", "a", "a"]


def test_drop_current_prevents_repeat_and_requeue():
    q = TrackQueue()
    q.loop_mode = "queue"
    q.add([make_track("a"), make_track("b")])
    q.advance()
    q.drop_current()
    assert q.advance().title == "b"
    assert names(q.upcoming) == []  # "a" was dropped, not re-appended

    q2 = TrackQueue()
    q2.loop_mode = "track"
    q2.add([make_track("a"), make_track("b")])
    q2.advance()
    q2.drop_current()
    assert q2.advance().title == "b"


def test_remove_by_position():
    q = TrackQueue()
    q.add([make_track("a"), make_track("b"), make_track("c")])
    assert q.remove(2).title == "b"
    assert names(q.upcoming) == ["a", "c"]
    with pytest.raises(IndexError):
        q.remove(0)
    with pytest.raises(IndexError):
        q.remove(3)


def test_clear_keeps_current():
    q = TrackQueue()
    q.add([make_track("a"), make_track("b"), make_track("c")])
    q.advance()
    assert q.clear() == 2
    assert q.current.title == "a"
    assert len(q.upcoming) == 0


def test_shuffle_keeps_same_tracks():
    q = TrackQueue()
    q.add([make_track(str(i)) for i in range(50)])
    before = names(q.upcoming)
    q.shuffle()
    after = names(q.upcoming)
    assert sorted(after) == sorted(before)
    assert after != before  # 1 in 50! chance of a false failure


def test_format_duration():
    assert format_duration(None) == "?:??"
    assert format_duration(5) == "0:05"
    assert format_duration(185) == "3:05"
    assert format_duration(3725) == "1:02:05"


# ---- GuildPlayer with fakes -----------------------------------------------


class FakeSource:
    def __init__(self, url, volume):
        self.url = url
        self.volume = volume


class FakeVoice:
    """Mimics discord.VoiceClient: play() keeps going until stop() or finish()."""

    def __init__(self):
        self.playing = None
        self.paused = False
        self.after = None
        self.played_urls = []
        self.connected = True
        self.channel = None

    @property
    def source(self):
        return self.playing

    def is_connected(self):
        return self.connected

    def is_playing(self):
        return self.playing is not None and not self.paused

    def is_paused(self):
        return self.playing is not None and self.paused

    def play(self, source, after):
        assert self.playing is None, "play() while already playing"
        self.playing = source
        self.after = after
        self.played_urls.append(source.url)

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop(self):
        self.finish()

    def finish(self):
        """The track ended (naturally or via stop): call the after-callback."""
        if self.playing is None:
            return
        self.playing = None
        self.paused = False
        self.after(None)

    async def disconnect(self, force=False):
        self.connected = False


class FakeChannel:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(message)


BAD = {"bad", "bad2"}


def fake_resolver(page_url):
    name = page_url.rsplit("=", 1)[1]
    if name in BAD:
        raise ytdl.YTDLError("this video is unavailable.")
    return name.upper(), 100, f"stream://{name}"


async def settle():
    """Let scheduled tasks and to_thread calls finish."""
    for _ in range(20):
        await asyncio.sleep(0.01)


def make_player():
    loop = asyncio.get_running_loop()
    destroyed = []
    player = GuildPlayer(1, loop, resolver=fake_resolver, source_factory=FakeSource, on_destroy=destroyed.append)
    player.voice = FakeVoice()
    player.text_channel = FakeChannel()
    return player, destroyed


def run(coro):
    asyncio.run(coro)


def test_player_plays_queue_in_order():
    async def scenario():
        player, _ = make_player()
        player.enqueue([make_track("a"), make_track("b")])
        await settle()
        assert player.voice.played_urls == ["stream://a"]
        assert player.queue.current.title == "A"  # title filled in from resolver
        player.voice.finish()
        await settle()
        assert player.voice.played_urls == ["stream://a", "stream://b"]
        player.voice.finish()
        await settle()
        assert player.queue.current is None
        assert player._idle_task is not None  # idle timer started
        await player.destroy()

    run(scenario())


def test_bad_track_is_skipped_and_queue_continues():
    async def scenario():
        player, _ = make_player()
        player.enqueue([make_track("bad"), make_track("bad2"), make_track("c")])
        await settle()
        assert player.voice.played_urls == ["stream://c"]
        skips = [m for m in player.text_channel.messages if m.startswith("Skipping")]
        assert len(skips) == 2
        await player.destroy()

    run(scenario())


def test_skip_goes_to_next_track_even_in_track_loop():
    async def scenario():
        player, _ = make_player()
        player.queue.loop_mode = "track"
        player.enqueue([make_track("a"), make_track("b")])
        await settle()
        player.voice.finish()  # natural end: repeat
        await settle()
        assert player.voice.played_urls == ["stream://a", "stream://a"]
        assert player.skip()
        await settle()
        assert player.voice.played_urls[-1] == "stream://b"
        await player.destroy()

    run(scenario())


def test_stop_clears_queue_and_does_not_continue():
    async def scenario():
        player, _ = make_player()
        player.enqueue([make_track("a"), make_track("b")])
        await settle()
        player.stop()
        await settle()
        assert player.voice.played_urls == ["stream://a"]
        assert player.queue.current is None
        assert len(player.queue.upcoming) == 0
        await player.destroy()

    run(scenario())


def test_enqueue_while_playing_does_not_interrupt():
    async def scenario():
        player, _ = make_player()
        player.enqueue([make_track("a")])
        await settle()
        assert player.enqueue([make_track("b")]) == 1
        await settle()
        assert player.voice.played_urls == ["stream://a"]
        player.voice.finish()
        await settle()
        assert player.voice.played_urls == ["stream://a", "stream://b"]
        await player.destroy()

    run(scenario())


def test_rapid_enqueues_while_idle_start_only_once():
    async def scenario():
        player, _ = make_player()
        player.enqueue([make_track("a")])
        player.enqueue([make_track("b")])
        player.enqueue([make_track("c")])
        await settle()
        assert player.voice.played_urls == ["stream://a"]
        assert names(player.queue.upcoming) == ["b", "c"]
        await player.destroy()

    run(scenario())


def test_volume_applies_to_current_source():
    async def scenario():
        player, _ = make_player()
        player.enqueue([make_track("a")])
        await settle()
        # FakeSource is not a PCMVolumeTransformer, so only the stored volume changes;
        # new tracks pick it up.
        player.set_volume(0.2)
        player.voice.finish()
        player.enqueue([make_track("b")])
        await settle()
        assert player.voice.source.volume == 0.2
        player.set_volume(3)
        assert player.volume == 1.0
        await player.destroy()

    run(scenario())


def test_destroy_disconnects_and_notifies():
    async def scenario():
        player, destroyed = make_player()
        voice = player.voice
        player.enqueue([make_track("a"), make_track("b")])
        await settle()
        await player.destroy()
        await settle()
        assert destroyed == [1]
        assert not voice.connected
        assert voice.played_urls == ["stream://a"]  # after-callback didn't start "b"

    run(scenario())
