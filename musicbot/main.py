"""Entry point: python -m musicbot.main"""

import inspect
import logging
import shutil
import sys

import discord
from discord.ext import commands

from musicbot import config

log = logging.getLogger("musicbot")


class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        # The prefix is never used; all commands are slash commands.
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, help_command=None)
        self.players: dict = {}  # guild id -> GuildPlayer

    async def setup_hook(self):
        await self.load_extension("musicbot.commands")
        synced = await self.tree.sync()
        log.info("Synced %d slash commands", len(synced))

    async def on_ready(self):
        log.info("Logged in as %s (id %s). Ready.", self.user, self.user.id)


def davey_is_incompatible() -> bool:
    """True if davey wants prepare/execute_transition calls that discord.py never makes.

    With davey 0.2+ and discord.py 2.7.1 the DAVE session never gets an encryption
    key, so discord.py sends unencrypted audio that listeners silently drop.
    """
    try:
        import davey
        import discord.gateway
        import discord.voice_state
    except ImportError:
        return False
    if not hasattr(davey.DaveSession, "prepare_transition"):
        return False
    try:
        sources = inspect.getsource(discord.gateway) + inspect.getsource(discord.voice_state)
    except (OSError, TypeError):
        return False
    return "prepare_transition" not in sources


def check_requirements() -> None:
    if shutil.which("ffmpeg") is None:
        sys.exit(
            "ffmpeg was not found on PATH. Install it (https://ffmpeg.org/download.html) "
            "and make sure the `ffmpeg` command works in a terminal."
        )
    if davey_is_incompatible():
        log.warning(
            "The installed `davey` needs DAVE transitions that this discord.py version doesn't perform. "
            "The bot will connect but nobody will hear it. Fix: pip install \"davey<0.2\" "
            "(or a discord.py release that supports newer davey)."
        )
    if not config.DISCORD_TOKEN:
        sys.exit(
            "DISCORD_TOKEN is not set. Copy .env.example to .env and put your bot token in it."
        )


class DaveLogFilter(logging.Filter):
    """Let through discord.gateway's normal INFO+ lines and its DAVE/MLS debug lines.

    The rest of that logger at DEBUG dumps websocket frames, including the
    voice session token, so this is an allowlist on the message template.
    """

    KEYWORDS = ("DAVE", "MLS", "binary frame", "transition")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.INFO:
            return True
        return isinstance(record.msg, str) and any(k in record.msg for k in self.KEYWORDS)


def enable_voice_debug_logging() -> None:
    for name in ("discord.player", "discord.voice_state", "discord.voice_client"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    gateway = logging.getLogger("discord.gateway")
    gateway.setLevel(logging.DEBUG)
    gateway.addFilter(DaveLogFilter())

    # discord.py silently swallows a failed MLS commit/welcome and restarts the
    # DAVE session. Log it so a handshake loop is visible.
    from discord.voice_state import VoiceConnectionState

    original = VoiceConnectionState._recover_from_invalid_commit

    async def logged_recover(self, transition_id: int) -> None:
        log.warning("DAVE: failed to process MLS commit/welcome for transition %d, resetting session", transition_id)
        await original(self, transition_id)

    VoiceConnectionState._recover_from_invalid_commit = logged_recover
    log.info("DEBUG_VOICE is on")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if config.DEBUG_VOICE:
        enable_voice_debug_logging()
    check_requirements()
    bot = MusicBot()
    # log_handler=None: keep our logging setup instead of discord.py's default.
    try:
        bot.run(config.DISCORD_TOKEN, log_handler=None)
    except discord.LoginFailure:
        sys.exit("Discord rejected the token. Check DISCORD_TOKEN in .env (reset it in the Developer Portal if needed).")


if __name__ == "__main__":
    main()
