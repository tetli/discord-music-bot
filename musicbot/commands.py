"""Slash commands."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from musicbot import ytdl
from musicbot.player import GuildPlayer, format_duration

log = logging.getLogger(__name__)

HELP_TEXT = """\
**Music bot commands**
`/play <query>` - play a YouTube link or search
`/join` - join your voice channel
`/pause` / `/resume` - pause or resume
`/skip` - skip the current track
`/stop` - stop and clear the queue
`/leave` - stop and leave the voice channel
`/help` - this list
"""


def esc(text: str) -> str:
    return discord.utils.escape_markdown(text)


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
        await interaction.response.defer()
        requested_by = interaction.user.display_name
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

    # ---- errors ---------------------------------------------------------------

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        log.exception("Error in /%s", interaction.command.name if interaction.command else "?", exc_info=error)
        try:
            await reply(interaction, "Something went wrong. Check the bot's console for details.", ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
