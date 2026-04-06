"""
Entry point for the recruitment / onboarding Discord bot.

Loads cogs, connects SQLite, registers persistent UI views, and syncs slash commands.
"""

from __future__ import annotations

import asyncio
import logging
from dotenv import load_dotenv
import os
import sys
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from utils.database import Database

# Ensure local imports resolve when running as `python bot.py`
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

COGS = (
    "cogs.onboarding",
    "cogs.interview",
    "cogs.verification",
    "cogs.reaction_roles",
    "cogs.admin",
    "tasks.timeout_tasks",
)

LOG = logging.getLogger("recruitment_bot")


def _log_synced_commands(synced: list[app_commands.AppCommand], scope: str) -> None:
    """Print each slash command name to the terminal after sync."""
    if not synced:
        LOG.warning("No slash commands in sync result (%s).", scope)
        return
    LOG.info("Slash commands loaded (%s) — %d total:", scope, len(synced))
    for cmd in sorted(synced, key=lambda c: c.name):
        LOG.info("  /%s", cmd.name)


def _intents() -> discord.Intents:
    intents = discord.Intents.default()
    # Privileged: must be enabled in Developer Portal → Bot → Privileged Gateway Intents
    intents.members = True  # on_member_join, role assignment (see cogs/onboarding.py)
    intents.guilds = True
    return intents


class RecruitmentBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=_intents())
        self.db = Database()

    async def setup_hook(self) -> None:
        await self.db.connect()
        for ext in COGS:
            await self.load_extension(ext)

        # Optional: set DISCORD_GUILD_ID to sync commands to one guild (faster while testing).
        guild_raw = os.getenv("DISCORD_GUILD_ID")
        if guild_raw:
            guild = discord.Object(id=int(guild_raw))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            _log_synced_commands(synced, f"guild {guild_raw}")
        else:
            synced = await self.tree.sync()
            _log_synced_commands(synced, "global")

    async def close(self) -> None:
        await self.db.close()
        await super().close()


async def main() -> None:
    load_dotenv(_ROOT / ".env")
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        LOG.error("Set DISCORD_BOT_TOKEN in your environment.")
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    bot = RecruitmentBot()

    @bot.tree.error
    async def on_app_command_error(
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        LOG.exception("Slash command error: %s", error)
        embed = discord.Embed(
            title="Something went wrong",
            description="An unexpected error occurred. Staff have been notified via logs.",
            color=discord.Color.red(),
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.event
    async def on_ready() -> None:
        assert bot.user
        LOG.info("Logged in as %s (%s)", bot.user, bot.user.id)

    async with bot:
        try:
            await bot.start(token)
        except discord.PrivilegedIntentsRequired:
            LOG.error(
                "This bot uses the privileged Server Members intent (member join handling). "
                "Open https://discord.com/developers/applications → your app → Bot → "
                "Privileged Gateway Intents → enable **Server Members Intent**, then restart."
            )
            raise
        except asyncio.CancelledError:
            # Ctrl+C / task cancellation: gateway aiohttp receive is cancelled; avoid noisy tracebacks.
            LOG.info("Connection closed (shutdown).")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Final exception from asyncio.run after cancellation chain on Ctrl+C.
        LOG.info("Shutdown requested (KeyboardInterrupt).")
        sys.exit(0)
