"""Slash commands."""

import discord
from discord import app_commands
from discord.ext import commands

HELP_TEXT = """\
**Music bot commands**
`/help` - this list
"""


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(description="Show the list of commands")
    async def help(self, interaction: discord.Interaction):
        await interaction.response.send_message(HELP_TEXT, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
