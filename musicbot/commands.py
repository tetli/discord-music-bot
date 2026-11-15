"""Slash commands."""

from __future__ import annotations

import asyncio
import logging
import math
import random
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from musicbot import config, playlists, ytdl
from musicbot.player import GuildPlayer, Track, format_duration

log = logging.getLogger(__name__)

HELP_TEXT = """\
**Music bot commands**
`/play <query>` - play a YouTube link, playlist link, or search
`/playlist load <name> [shuffle]` - queue a playlist file
`/playlist list` - list playlist files
`/playlist add <name> <url>` - add a link to a playlist file
`/join` - join your voice channel
`/pause` / `/resume` - pause or resume
`/skip` - skip the current track
`/stop` - stop and clear the queue
`/leave` - stop and leave the voice channel
`/queue [page]` - show the queue
`/nowplaying` - show the current track
`/remove <position>` - remove a track from the queue
`/clear` - clear the queue (keeps the current track)
`/shuffle` - shuffle the queue
`/loop <off|track|queue>` - set the loop mode
`/volume <0-100>` - set the volume
`/help` - this list
"""


def esc(text: str) -> str:
    return discord.utils.escape_markdown(text)


def short(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def queue_entry(track: Track) -> str:
    """One /queue line: title and duration, or the link if the title isn't known."""
    if track.title:
        return f"{esc(short(track.title))} [{format_duration(track.duration)}]"
    # <...> stops Discord from turning the link into a preview.
    note = " (couldn't load info)" if track.lookup == "failed" else ""
    return f"<{track.page_url}>{note}"


async def reply(interaction: discord.Interaction, message: str, *, ephemeral: bool = False) -> None:
    """Respond to an interaction whether or not it was deferred."""
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(message, ephemeral=ephemeral)


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players: dict[int, GuildPlayer] = bot.players

    # ---- helpers -------------------------------------------------------------

    def get_player(self, guild: discord.Guild) -> GuildPlayer | None:
        player = self.players.get(guild.id)
        if player and player.destroyed:
            return None
        return player

    def get_or_create_player(self, guild: discord.Guild) -> GuildPlayer:
        player = self.get_player(guild)
        if player is None:
            player = GuildPlayer(guild.id, self.bot.loop, on_destroy=self._forget_player)
            self.players[guild.id] = player
        return player

    def _forget_player(self, guild_id: int) -> None:
        self.players.pop(guild_id, None)

    def join_target(self, interaction: discord.Interaction) -> tuple[discord.VoiceChannel | None, str | None]:
        """Pick the voice channel to join for this user. Returns (channel, error)."""
        member = interaction.user
        if not isinstance(member, discord.Member) or member.voice is None or member.voice.channel is None:
            return None, "Join a voice channel first."
        channel = member.voice.channel
        voice = interaction.guild.voice_client
        if voice and voice.is_connected() and voice.channel != channel:
            player = self.get_player(interaction.guild)
            if player and player.queue.current is not None:
                return None, f"I'm already playing in {voice.channel.mention}. Join me there."
        if voice is None or voice.channel != channel:
            perms = channel.permissions_for(interaction.guild.me)
            missing = [name for name, ok in (("Connect", perms.connect), ("Speak", perms.speak)) if not ok]
            if missing:
                return None, f"I need the {' and '.join(missing)} permission in {channel.mention}."
            if channel.user_limit and len(channel.members) >= channel.user_limit and not perms.move_members:
                return None, f"{channel.mention} is full."
        return channel, None

    async def connect(self, interaction: discord.Interaction, channel: discord.VoiceChannel) -> GuildPlayer | None:
        """Connect (or move) to channel and return the guild's player.

        On failure, tells the user and returns None.
        """
        guild = interaction.guild
        voice = guild.voice_client
        try:
            if voice is None or not voice.is_connected():
                voice = await channel.connect(self_deaf=True)
            elif voice.channel != channel:
                await voice.move_to(channel)
        except (asyncio.TimeoutError, discord.ClientException) as e:
            log.warning("Could not connect to voice in guild %s: %s", guild.id, e)
            await reply(interaction, f"I couldn't connect to {channel.mention}. Try again in a moment.")
            return None
        player = self.get_or_create_player(guild)
        player.voice = voice
        player.text_channel = interaction.channel
        player.update_alone_timer()
        return player

    async def control_player(self, interaction: discord.Interaction) -> GuildPlayer | None:
        """For control commands: the user must be in the bot's voice channel."""
        voice = interaction.guild.voice_client
        player = self.get_player(interaction.guild)
        if voice is None or player is None:
            await reply(interaction, "I'm not in a voice channel.", ephemeral=True)
            return None
        member = interaction.user
        if not isinstance(member, discord.Member) or member.voice is None or member.voice.channel != voice.channel:
            await reply(interaction, f"You need to be in {voice.channel.mention} to do that.", ephemeral=True)
            return None
        player.text_channel = interaction.channel
        return player

    # ---- commands -------------------------------------------------------------

    @app_commands.command(description="Show the list of commands")
    async def help(self, interaction: discord.Interaction):
        await interaction.response.send_message(HELP_TEXT, ephemeral=True)

    @app_commands.command(description="Join your voice channel")
    @app_commands.guild_only()
    async def join(self, interaction: discord.Interaction):
        channel, error = self.join_target(interaction)
        if error:
            await reply(interaction, error, ephemeral=True)
            return
        await interaction.response.defer()
        player = await self.connect(interaction, channel)
        if player is None:
            return
        if player.queue.current is None:
            player.start_idle_timer()
        await reply(interaction, f"Joined {channel.mention}.")

    @app_commands.command(description="Play a YouTube link or search YouTube")
    @app_commands.describe(query="A YouTube URL or search text")
    @app_commands.guild_only()
    async def play(self, interaction: discord.Interaction, query: str):
        query = query.strip()
        channel, error = self.join_target(interaction)
        if error:
            await reply(interaction, error, ephemeral=True)
            return
        if query.startswith(("http://", "https://")) and not playlists.is_youtube_url(query):
            await reply(interaction, "I can only play YouTube links (or search text).", ephemeral=True)
            return
        await interaction.response.defer()
        requested_by = interaction.user.display_name

        if playlists.is_playlist_url(query):
            try:
                tracks = await asyncio.to_thread(ytdl.expand_playlist, query, requested_by)
            except ytdl.YTDLError as e:
                await reply(interaction, f"Couldn't load that playlist: {e}")
                return
            capped = len(tracks) > config.MAX_PLAYLIST_ENTRIES
            tracks = tracks[: config.MAX_PLAYLIST_ENTRIES]
            player = await self.connect(interaction, channel)
            if player is None:
                return
            position = player.enqueue(tracks)
            message = f"Queued {len(tracks)} tracks from the playlist (starting at position {position})."
            if capped:
                message += f" The playlist was cut off at {config.MAX_PLAYLIST_ENTRIES} tracks."
            await reply(interaction, message)
            return

        try:
            track = await asyncio.to_thread(ytdl.search_or_resolve, query, requested_by)
        except ytdl.YTDLError as e:
            await reply(interaction, f"Couldn't play that: {e}")
            return
        player = await self.connect(interaction, channel)
        if player is None:
            return
        was_idle = player.queue.current is None
        position = player.enqueue([track], announce=False)
        label = f"**{esc(track.label)}** [{format_duration(track.duration)}]"
        if was_idle and position == 1:
            await reply(interaction, f"Playing {label}")
        else:
            await reply(interaction, f"Queued {label} at position {position}.")

    @app_commands.command(description="Skip the current track")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction):
        player = await self.control_player(interaction)
        if player is None:
            return
        current = player.queue.current
        if player.skip():
            await reply(interaction, f"Skipped **{esc(current.label)}**." if current else "Skipped.")
        else:
            await reply(interaction, "Nothing is playing.", ephemeral=True)

    @app_commands.command(description="Pause playback")
    @app_commands.guild_only()
    async def pause(self, interaction: discord.Interaction):
        player = await self.control_player(interaction)
        if player is None:
            return
        if player.pause():
            await reply(interaction, "Paused.")
        else:
            await reply(interaction, "Nothing is playing.", ephemeral=True)

    @app_commands.command(description="Resume playback")
    @app_commands.guild_only()
    async def resume(self, interaction: discord.Interaction):
        player = await self.control_player(interaction)
        if player is None:
            return
        if player.resume():
            await reply(interaction, "Resumed.")
        else:
            await reply(interaction, "Nothing is paused.", ephemeral=True)

    @app_commands.command(description="Stop playback and clear the queue")
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction):
        player = await self.control_player(interaction)
        if player is None:
            return
        player.stop()
        await reply(interaction, "Stopped and cleared the queue.")

    @app_commands.command(description="Stop, clear the queue and leave the voice channel")
    @app_commands.guild_only()
    async def leave(self, interaction: discord.Interaction):
        player = await self.control_player(interaction)
        if player is None:
            return
        await player.destroy()
        await reply(interaction, "Bye!")

    @app_commands.command(name="queue", description="Show the queue")
    @app_commands.describe(page="Page number (10 tracks per page)")
    @app_commands.guild_only()
    async def queue_(self, interaction: discord.Interaction, page: app_commands.Range[int, 1] = 1):
        player = self.get_player(interaction.guild)
        if player is None or (player.queue.current is None and not player.queue.upcoming):
            await reply(interaction, "The queue is empty.", ephemeral=True)
            return
        queue = player.queue
        upcoming = list(queue.upcoming)
        pages = max(1, math.ceil(len(upcoming) / config.QUEUE_PAGE_SIZE))
        page = min(page, pages)
        start = (page - 1) * config.QUEUE_PAGE_SIZE
        page_tracks = upcoming[start : start + config.QUEUE_PAGE_SIZE]

        if any(t.needs_metadata for t in page_tracks):
            await interaction.response.defer()
            await player.ensure_metadata(page_tracks, timeout=config.QUEUE_METADATA_TIMEOUT)

        lines = []
        if queue.current:
            state = "Paused" if player.is_paused else "Now playing"
            lines.append(f"**{state}:** {esc(short(queue.current.label))} [{format_duration(queue.current.duration)}]")
        if upcoming:
            lines.append("")
            for i, track in enumerate(page_tracks, start=start + 1):
                lines.append(f"`{i}.` {queue_entry(track)}")
            known = [t.duration for t in upcoming if t.duration is not None]
            total = format_duration(sum(known)) + ("+" if len(known) < len(upcoming) else "")
            lines.append("")
            lines.append(f"{len(upcoming)} tracks in queue, {total} total. Page {page}/{pages}.")
        else:
            lines.append("Nothing else is queued.")
        if queue.loop_mode != "off":
            lines.append(f"Loop: {queue.loop_mode}")
        await reply(interaction, "\n".join(lines))

    @app_commands.command(description="Show the current track")
    @app_commands.guild_only()
    async def nowplaying(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild)
        track = player.queue.current if player else None
        if track is None:
            await reply(interaction, "Nothing is playing.", ephemeral=True)
            return
        state = " (paused)" if player.is_paused else ""
        await reply(
            interaction,
            f"**{esc(track.label)}**{state}\n"
            f"<{track.page_url}>\n"
            f"Duration: {format_duration(track.duration)} · Requested by {esc(track.requested_by)}",
        )

    @app_commands.command(description="Remove a track from the queue")
    @app_commands.describe(position="Position in the queue (see /queue)")
    @app_commands.guild_only()
    async def remove(self, interaction: discord.Interaction, position: app_commands.Range[int, 1]):
        player = await self.control_player(interaction)
        if player is None:
            return
        try:
            track = player.queue.remove(position)
        except IndexError:
            await reply(interaction, f"There is no track at position {position}.", ephemeral=True)
            return
        player.warm_up_queue()
        await reply(interaction, f"Removed **{esc(track.label)}**.")

    @app_commands.command(description="Clear the queue but keep the current track playing")
    @app_commands.guild_only()
    async def clear(self, interaction: discord.Interaction):
        player = await self.control_player(interaction)
        if player is None:
            return
        count = player.queue.clear()
        await reply(interaction, f"Cleared {count} track{'s' if count != 1 else ''} from the queue.")

    @app_commands.command(description="Shuffle the queue")
    @app_commands.guild_only()
    async def shuffle(self, interaction: discord.Interaction):
        player = await self.control_player(interaction)
        if player is None:
            return
        if len(player.queue.upcoming) < 2:
            await reply(interaction, "Not enough tracks in the queue to shuffle.", ephemeral=True)
            return
        player.queue.shuffle()
        player.warm_up_queue()
        await reply(interaction, f"Shuffled {len(player.queue.upcoming)} tracks.")

    @app_commands.command(description="Set the loop mode")
    @app_commands.describe(mode="off, track (repeat current) or queue (repeat everything)")
    @app_commands.guild_only()
    async def loop(self, interaction: discord.Interaction, mode: Literal["off", "track", "queue"]):
        player = await self.control_player(interaction)
        if player is None:
            return
        player.queue.loop_mode = mode
        messages = {
            "off": "Loop is off.",
            "track": "Looping the current track.",
            "queue": "Looping the queue.",
        }
        await reply(interaction, messages[mode])

    @app_commands.command(description="Set the volume")
    @app_commands.describe(level="Volume from 0 to 100")
    @app_commands.guild_only()
    async def volume(self, interaction: discord.Interaction, level: app_commands.Range[int, 0, 100]):
        player = await self.control_player(interaction)
        if player is None:
            return
        player.set_volume(level / 100)
        await reply(interaction, f"Volume set to {level}%.")

    # ---- playlist files ---------------------------------------------------------

    playlist = app_commands.Group(name="playlist", description="Playlist files in the playlists folder", guild_only=True)

    @playlist.command(name="load", description="Queue all tracks from a playlist file")
    @app_commands.describe(name="Playlist name (file playlists/<name>.txt)", shuffle="Shuffle the tracks before queueing")
    async def playlist_load(self, interaction: discord.Interaction, name: str, shuffle: bool = False):
        channel, error = self.join_target(interaction)
        if error:
            await reply(interaction, error, ephemeral=True)
            return
        try:
            playlists.playlist_path(name)
        except playlists.PlaylistError as e:
            await reply(interaction, str(e), ephemeral=True)
            return
        await interaction.response.defer()
        try:
            parsed = await asyncio.to_thread(playlists.read_playlist, name)
        except playlists.PlaylistError as e:
            await reply(interaction, str(e))
            return

        requested_by = interaction.user.display_name
        tracks: list[Track] = []
        failed_playlists = 0
        for url in parsed.urls:
            if playlists.is_playlist_url(url):
                try:
                    tracks.extend(await asyncio.to_thread(ytdl.expand_playlist, url, requested_by))
                except ytdl.YTDLError:
                    failed_playlists += 1
            else:
                tracks.append(Track(page_url=url, title=None, duration=None, requested_by=requested_by))
        capped = parsed.capped or len(tracks) > config.MAX_PLAYLIST_ENTRIES
        tracks = tracks[: config.MAX_PLAYLIST_ENTRIES]

        notes = []
        if parsed.skipped_lines:
            shown = ", ".join(str(n) for n in parsed.skipped_lines[:20])
            more = "…" if len(parsed.skipped_lines) > 20 else ""
            count = len(parsed.skipped_lines)
            notes.append(f"Skipped {count} invalid line{'s' if count != 1 else ''} (line {shown}{more}).")
        if failed_playlists:
            notes.append(f"Couldn't load {failed_playlists} YouTube playlist link{'s' if failed_playlists != 1 else ''}.")
        if capped:
            notes.append(f"Stopped at the limit of {config.MAX_PLAYLIST_ENTRIES} tracks.")

        if not tracks:
            await reply(interaction, " ".join([f"`{name}` has no playable entries."] + notes))
            return
        if shuffle:
            random.shuffle(tracks)
        player = await self.connect(interaction, channel)
        if player is None:
            return
        player.enqueue(tracks)
        shuffled = " (shuffled)" if shuffle else ""
        message = f"Queued {len(tracks)} track{'s' if len(tracks) != 1 else ''} from `{name}`{shuffled}."
        await reply(interaction, " ".join([message] + notes))

    @playlist_load.autocomplete("name")
    async def playlist_name_autocomplete(self, interaction: discord.Interaction, current: str):
        current = current.lower()
        names = [n for n in playlists.playlist_names() if current in n.lower()]
        return [app_commands.Choice(name=n, value=n) for n in names[:25]]

    @playlist.command(name="list", description="List the playlist files")
    async def playlist_list(self, interaction: discord.Interaction):
        found = await asyncio.to_thread(playlists.list_playlists)
        if not found:
            await reply(interaction, "No playlists yet. Put `.txt` files in the `playlists` folder.", ephemeral=True)
            return
        lines = [f"`{name}` - {count} entr{'ies' if count != 1 else 'y'}" for name, count in found]
        text = "**Playlists**\n" + "\n".join(lines)
        if len(text) > 1900:
            text = text[:1900] + "\n…"
        await reply(interaction, text)

    @playlist.command(name="add", description="Add a YouTube link to a playlist file (creates it if needed)")
    @app_commands.describe(name="Playlist name", url="YouTube video or playlist URL")
    async def playlist_add(self, interaction: discord.Interaction, name: str, url: str):
        try:
            created = await asyncio.to_thread(playlists.add_to_playlist, name, url)
        except playlists.PlaylistError as e:
            await reply(interaction, str(e), ephemeral=True)
            return
        if created:
            await reply(interaction, f"Created playlist `{name}` and added <{url.strip()}>.")
        else:
            await reply(interaction, f"Added <{url.strip()}> to `{name}`.")

    playlist_add.autocomplete("name")(playlist_name_autocomplete)

    # ---- voice events -------------------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        player = self.get_player(member.guild)
        if player is None:
            return
        if member.id == self.bot.user.id:
            if after.channel is None:
                # Disconnected by a user (or by us, in which case the player is already gone).
                log.info("Disconnected from voice in guild %s", member.guild.id)
                await player.destroy()
            else:
                player.update_alone_timer()  # moved to another channel
            return
        bot_channel = player.voice.channel if player.voice else None
        if bot_channel is not None and bot_channel in (before.channel, after.channel):
            player.update_alone_timer()

    # ---- errors ---------------------------------------------------------------

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        log.exception("Error in /%s", interaction.command.name if interaction.command else "?", exc_info=error)
        try:
            await reply(interaction, "Something went wrong. Check the bot's console for details.", ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
