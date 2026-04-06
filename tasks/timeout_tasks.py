"""
Interview timeout: ban applicants who stay in interview state past 72 hours without resolution.
"""

from __future__ import annotations

import logging
import time

import discord
from discord.ext import commands, tasks

from cogs.verification import _remove_interview_role_if_present
from utils.database import STATUS_DENIED, Database
from utils.recruitment_embeds import build_application_embed, send_log_embed

logger = logging.getLogger(__name__)

INTERVIEW_TIMEOUT_SECONDS = 72 * 60 * 60


class TimeoutTasks(commands.Cog):
    """Periodic cleanup for stale interviews."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.check_stale_interviews.start()

    def cog_unload(self) -> None:
        self.check_stale_interviews.cancel()

    @tasks.loop(minutes=10)
    async def check_stale_interviews(self) -> None:
        db: Database = self.bot.db  # type: ignore[attr-defined]
        cutoff = int(time.time()) - INTERVIEW_TIMEOUT_SECONDS
        stale = await db.list_interview_timeouts(cutoff)
        for app in stale:
            await self._process_timeout(db, app)

    @check_stale_interviews.before_loop
    async def _before_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _process_timeout(self, db: Database, app) -> None:
        guild = self.bot.get_guild(app.guild_id)
        if not guild:
            logger.warning("Timeout: guild %s missing for app %s", app.guild_id, app.id)
            return

        settings = await db.get_guild_settings(app.guild_id)

        tm = guild.get_member(app.user_id)
        if tm is None:
            try:
                tm = await guild.fetch_member(app.user_id)
            except discord.NotFound:
                tm = None
        if tm:
            await _remove_interview_role_if_present(
                guild, tm, settings, reason="Interview stage timeout (72h)"
            )

        try:
            await guild.ban(
                discord.Object(id=app.user_id),
                reason="Interview stage timeout (72h)",
                delete_message_seconds=0,
            )
        except discord.HTTPException as e:
            logger.warning("Timeout ban failed app=%s user=%s: %s", app.id, app.user_id, e)

        await db.update_application_status(app.id, status=STATUS_DENIED)

        user = self.bot.get_user(app.user_id)
        member = guild.get_member(app.user_id)

        applicant_mention = tm.mention if tm else f"<@{app.user_id}>"
        await send_log_embed(
            guild,
            settings,
            "Interview timeout",
            f"Application `#{app.id}` — applicant {applicant_mention} — banned after 72h in interview with no resolution.",
            discord.Color.dark_red(),
        )

        if app.thread_id:
            ch = guild.get_channel(app.thread_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    await ch.delete(reason="Interview timeout (72h)")
                except discord.HTTPException:
                    logger.exception("Could not delete interview text channel for timeout app %s", app.id)
            else:
                thread = guild.get_thread(app.thread_id)
                if thread:
                    try:
                        await thread.delete()
                    except discord.HTTPException:
                        logger.exception("Could not delete interview thread for timeout app %s", app.id)

        if app.staff_channel_id and app.staff_message_id:
            ch = guild.get_channel(app.staff_channel_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    msg = await ch.fetch_message(app.staff_message_id)
                    app2 = await db.get_application_by_id(app.id)
                    if app2:
                        await msg.edit(
                            embed=build_application_embed(
                                app2,
                                member=member,
                                user=user,
                                status_line="**Timed out** — applicant banned after 72h in interview.",
                                color=discord.Color.dark_red(),
                            ),
                            view=None,
                        )
                except discord.HTTPException:
                    logger.exception("Could not edit staff message for timeout app %s", app.id)

        logger.info("Interview timeout processed for application %s", app.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TimeoutTasks(bot))
