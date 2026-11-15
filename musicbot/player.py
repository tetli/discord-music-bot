"""Track, the queue logic, and the per-guild player.

TrackQueue is pure logic (no Discord, no network) so it can be unit tested.
GuildPlayer wires it to a voice client; tests can pass fakes for the voice
client, the stream resolver and the audio source factory.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import deque
from dataclasses import dataclass
from typing import Callable, Literal

import discord

from musicbot import config, ytdl

log = logging.getLogger(__name__)

LoopMode = Literal["off", "track", "queue"]


@dataclass
class Track:
    page_url: str  # the YouTube watch URL (stable, unlike stream URLs)
    title: str | None
    duration: int | None  # seconds
    requested_by: str  # display name

    @property
    def label(self) -> str:
        return self.title or self.page_url


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "?:??"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class TrackQueue:
    """The upcoming tracks, the current track and the loop mode."""

    def __init__(self):
        self.upcoming: deque[Track] = deque()
        self.current: Track | None = None
        self.loop_mode: LoopMode = "off"

    def add(self, tracks: list[Track]) -> int:
        """Append tracks. Returns the 1-based queue position of the first one."""
        position = len(self.upcoming) + 1
        self.upcoming.extend(tracks)
        return position

    def advance(self, *, skipped: bool = False) -> Track | None:
        """Move to the next track according to the loop mode and return it.

        loop "track" repeats the current track unless it was skipped.
        loop "queue" puts the finished track back at the end of the queue.
        """
        finished = self.current
        if finished is not None:
            if self.loop_mode == "track" and not skipped:
                return finished
            if self.loop_mode == "queue":
                self.upcoming.append(finished)
        self.current = self.upcoming.popleft() if self.upcoming else None
        return self.current

    def drop_current(self) -> None:
        """Forget the current track so advance() won't repeat or re-queue it."""
        self.current = None

    def remove(self, position: int) -> Track:
        """Remove the track at a 1-based position. Raises IndexError."""
        if not 1 <= position <= len(self.upcoming):
            raise IndexError(position)
        track = self.upcoming[position - 1]
        del self.upcoming[position - 1]
        return track

    def clear(self) -> int:
        """Clear upcoming tracks (the current one keeps playing). Returns how many."""
        count = len(self.upcoming)
        self.upcoming.clear()
        return count

    def shuffle(self) -> None:
        items = list(self.upcoming)
        random.shuffle(items)
        self.upcoming = deque(items)

    def reset(self) -> None:
        self.upcoming.clear()
        self.current = None


def make_source(stream_url: str, volume: float) -> discord.AudioSource:
    audio = discord.FFmpegPCMAudio(
        stream_url,
        before_options=config.FFMPEG_BEFORE_OPTIONS,
        options=config.FFMPEG_OPTIONS,
    )
    return discord.PCMVolumeTransformer(audio, volume=volume)


class GuildPlayer:
    """Plays a guild's queue through its voice client."""

    def __init__(
        self,
        guild_id: int,
        loop: asyncio.AbstractEventLoop,
        *,
        resolver: Callable[[str], tuple[str | None, int | None, str]] | None = None,
        source_factory: Callable[[str, float], discord.AudioSource] = make_source,
        on_destroy: Callable[[int], None] | None = None,
    ):
        self.guild_id = guild_id
        self.loop = loop
        self.queue = TrackQueue()
        self.volume = config.DEFAULT_VOLUME
        self.voice: discord.VoiceClient | None = None
        self.text_channel: discord.abc.Messageable | None = None

        self._resolver = resolver or ytdl.resolve_stream
        self._source_factory = source_factory
        self._on_destroy = on_destroy
        self._lock = asyncio.Lock()
        self._skip_requested = False
        self._generation = 0  # bumped by stop() so in-flight starts are abandoned
        self._idle_task: asyncio.Task | None = None
        self._alone_task: asyncio.Task | None = None
        self.destroyed = False

    # ---- queue operations -------------------------------------------------

    def enqueue(self, tracks: list[Track], *, announce: bool = True) -> int:
        """Add tracks and start playing if idle. Returns the first one's position.

        announce=False skips the "Now playing" message for the first track,
        for when the command reply already says it.
        """
        position = self.queue.add(tracks)
        if self.queue.current is None:
            self.loop.create_task(self._start_if_idle(announce=announce))
        return position

    def skip(self) -> bool:
        if self.voice and (self.voice.is_playing() or self.voice.is_paused()):
            self._skip_requested = True
            self.voice.stop()  # triggers the after-callback, which plays the next track
            return True
        return False

    def stop(self) -> None:
        """Stop playback and clear the queue. Stays connected."""
        self._generation += 1
        self.queue.reset()
        if self.voice and (self.voice.is_playing() or self.voice.is_paused()):
            self.voice.stop()
        self.start_idle_timer()

    def pause(self) -> bool:
        if self.voice and self.voice.is_playing():
            self.voice.pause()
            return True
        return False

    def resume(self) -> bool:
        if self.voice and self.voice.is_paused():
            self.voice.resume()
            return True
        return False

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, volume))
        source = self.voice.source if self.voice else None
        if isinstance(source, discord.PCMVolumeTransformer):
            source.volume = self.volume

    @property
    def is_paused(self) -> bool:
        return bool(self.voice and self.voice.is_paused())

    # ---- playback -----------------------------------------------------------

    async def _start_if_idle(self, *, announce: bool = True) -> None:
        async with self._lock:
            if self.queue.current is None and not self.destroyed:
                await self._play_next_locked(skipped=False, announce=announce)

    async def _on_track_end(self) -> None:
        async with self._lock:
            skipped, self._skip_requested = self._skip_requested, False
            if not self.destroyed:
                await self._play_next_locked(skipped=skipped)

    def _after(self, error: Exception | None) -> None:
        # Runs in the voice thread, not the event loop: hand control back.
        if error:
            log.warning("Playback error in guild %s: %s", self.guild_id, error)
        if not self.destroyed:
            asyncio.run_coroutine_threadsafe(self._on_track_end(), self.loop)

    async def _play_next_locked(self, *, skipped: bool, announce: bool = True) -> None:
        generation = self._generation
        while True:
            track = self.queue.advance(skipped=skipped)
            skipped = False
            if track is None:
                self.start_idle_timer()
                return
            if not self.voice or not self.voice.is_connected():
                return
            self._cancel_idle_timer()

            try:
                title, duration, stream_url = await asyncio.to_thread(self._resolver, track.page_url)
            except ytdl.YTDLError as e:
                await self.send(f"Skipping **{discord.utils.escape_markdown(track.label)}**: {e}")
                self.queue.drop_current()
                announce = True
                continue
            except Exception:
                log.exception("Unexpected error resolving %s", track.page_url)
                await self.send(f"Skipping **{discord.utils.escape_markdown(track.label)}**: couldn't load this video.")
                self.queue.drop_current()
                announce = True
                continue

            if generation != self._generation or self.destroyed:
                return  # stopped while resolving
            if not self.voice or not self.voice.is_connected():
                return

            track.title = title or track.title
            track.duration = duration if duration is not None else track.duration
            try:
                self.voice.play(self._source_factory(stream_url, self.volume), after=self._after)
            except Exception:
                log.exception("Could not start playback of %s", track.page_url)
                await self.send(f"Skipping **{discord.utils.escape_markdown(track.label)}**: playback failed.")
                self.queue.drop_current()
                announce = True
                continue
            if config.DEBUG_VOICE:
                self.loop.create_task(self._log_voice_health(track, 5))
                self.loop.create_task(self._log_voice_health(track, 30))
            if announce:
                await self.send(
                    f"Now playing: **{discord.utils.escape_markdown(track.label)}** "
                    f"[{format_duration(track.duration)}]"
                )
            return

    async def _log_voice_health(self, track: Track, delay: float = 5.0) -> None:
        """Log what the voice client is doing a few seconds into a track (DEBUG_VOICE only).

        Uses discord.py internals; diagnostics only. If frames are being sent but
        dave_ready is False while dave_version > 0, listeners drop the audio.
        """
        await asyncio.sleep(delay)
        voice = self.voice
        if voice is None or self.queue.current is not track:
            return
        audio_player = getattr(voice, "_player", None)
        connection = getattr(voice, "_connection", None)
        dave_session = getattr(connection, "dave_session", None)
        dave_details = "no session"
        if dave_session is not None:
            try:
                stats = dave_session.get_encryption_stats()
                dave_details = (
                    f"status={dave_session.status} can_encrypt={dave_session.can_encrypt} "
                    f"epoch={dave_session.epoch} group_users={len(dave_session.get_user_ids())} "
                    f"encrypt_ok={stats.successes} encrypt_failed={stats.failures}"
                )
            except Exception as e:
                dave_details = f"unavailable ({e!r})"
        log.info(
            "Voice health guild=%s after %ss: connected=%s playing=%s paused=%s frames_sent=%s "
            "mode=%s dave_version=%s dave_ready=%s endpoint=%s dave: %s",
            self.guild_id,
            delay,
            voice.is_connected(),
            voice.is_playing(),
            voice.is_paused(),
            getattr(audio_player, "loops", None),
            getattr(voice, "mode", None),
            getattr(connection, "dave_protocol_version", None),
            getattr(dave_session, "ready", None),
            getattr(voice, "endpoint", None),
            dave_details,
        )

    # ---- messages, timers, cleanup -----------------------------------------

    async def send(self, message: str) -> None:
        if self.text_channel is None:
            return
        try:
            await self.text_channel.send(message)
        except discord.HTTPException as e:
            log.warning("Could not send message in guild %s: %s", self.guild_id, e)

    def start_idle_timer(self) -> None:
        self._cancel_idle_timer()
        if not self.destroyed:
            self._idle_task = self.loop.create_task(
                self._leave_after(config.IDLE_TIMEOUT, "Nothing has played for a while, so I left the voice channel.")
            )

    def _cancel_idle_timer(self) -> None:
        if self._idle_task and self._idle_task is not asyncio.current_task():
            self._idle_task.cancel()
        self._idle_task = None

    def update_alone_timer(self) -> None:
        """Start the alone timer if no humans are in the bot's channel, else cancel it."""
        channel = self.voice.channel if self.voice else None
        alone = channel is not None and not any(not m.bot for m in channel.members)
        if alone and self._alone_task is None and not self.destroyed:
            self._alone_task = self.loop.create_task(
                self._leave_after(config.ALONE_TIMEOUT, "Everyone left, so I left the voice channel too.")
            )
        elif not alone and self._alone_task is not None:
            self._alone_task.cancel()
            self._alone_task = None

    async def _leave_after(self, delay: float, message: str) -> None:
        await asyncio.sleep(delay)
        if not self.destroyed:
            await self.send(message)
            await self.destroy()

    async def destroy(self) -> None:
        """Stop everything, disconnect, and forget this player."""
        if self.destroyed:
            return
        self.destroyed = True
        self._generation += 1
        self.queue.reset()
        current = asyncio.current_task()
        for task in (self._idle_task, self._alone_task):
            if task and task is not current:
                task.cancel()
        self._idle_task = self._alone_task = None
        if self.voice:
            try:
                if self.voice.is_playing() or self.voice.is_paused():
                    self.voice.stop()
                if self.voice.is_connected():
                    await self.voice.disconnect(force=True)
            except Exception:
                log.exception("Error while disconnecting in guild %s", self.guild_id)
        self.voice = None
        if self._on_destroy:
            self._on_destroy(self.guild_id)
