"""Entry point: python -m musicbot.main"""

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


def check_requirements() -> None:
    if shutil.which("ffmpeg") is None:
        sys.exit(
            "ffmpeg was not found on PATH. Install it (https://ffmpeg.org/download.html) "
            "and make sure the `ffmpeg` command works in a terminal."
        )
    if not config.DISCORD_TOKEN:
        sys.exit(
            "DISCORD_TOKEN is not set. Copy .env.example to .env and put your bot token in it."
        )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    check_requirements()
    bot = MusicBot()
    # log_handler=None: keep our logging setup instead of discord.py's default.
    try:
        bot.run(config.DISCORD_TOKEN, log_handler=None)
    except discord.LoginFailure:
        sys.exit("Discord rejected the token. Check DISCORD_TOKEN in .env (reset it in the Developer Portal if needed).")


if __name__ == "__main__":
    main()
