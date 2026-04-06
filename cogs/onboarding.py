"""
Member join handling: optional welcome DM and unverified role assignment.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from utils.branding import brand_user_embed
from utils.database import Database
from utils.recruitment_embeds import send_log_embed

logger = logging.getLogger(__name__)


class Onboarding(commands.Cog):
    """Welcomes new members according to guild settings."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        try:
            settings = await self.db.get_guild_settings(member.guild.id)
        except Exception:
            logger.exception("Failed to load guild settings for %s", member.guild.id)
            return

        await send_log_embed(
            member.guild,
            settings,
            "Member joined",
            f"{member.mention} joined the server.",
            discord.Color.green(),
        )

        # Optional unverified role
        if settings.unverified_role_id:
            role = member.guild.get_role(settings.unverified_role_id)
            if role:
                try:
                    await member.add_roles(role, reason="Onboarding: unverified role")
                except discord.HTTPException:
                    logger.exception(
                        "Could not assign unverified role %s to %s",
                        settings.unverified_role_id,
                        member.id,
                    )
            else:
                logger.warning(
                    "Unverified role id %s not found in guild %s",
                    settings.unverified_role_id,
                    member.guild.id,
                )

        if not settings.welcome_enabled or not (settings.welcome_message or "").strip():
            return

        text = settings.welcome_message
        verification_ch = (
            member.guild.get_channel(settings.verification_channel_id)
            if settings.verification_channel_id
            else None
        )
        staff_ch = member.guild.get_channel(settings.staff_channel_id) if settings.staff_channel_id else None
        verification_mention = verification_ch.mention if isinstance(verification_ch, discord.abc.GuildChannel) else ""
        staff_mention = staff_ch.mention if isinstance(staff_ch, discord.abc.GuildChannel) else ""

        text = text.replace("{user}", member.mention)
        text = text.replace("{server}", member.guild.name)
        text = text.replace("{verification_channel}", verification_mention)
        text = text.replace("{staff_channel}", staff_mention)

        body = text.strip()[:4000]
        welcome_embed = brand_user_embed(title=f"Welcome to {member.guild.name}", description=body)

        # Post into the configured welcome message channel; fallback to DM if missing.
        try:
            welcome_ch = (
                member.guild.get_channel(settings.welcome_channel_id)
                if settings.welcome_channel_id
                else None
            )
            if isinstance(welcome_ch, discord.abc.Messageable):
                await welcome_ch.send(embed=welcome_embed)
                return
        except discord.HTTPException:
            logger.exception("Failed sending welcome message to configured welcome channel for %s", member.id)

        # Fallback: DM the welcome embed if the channel cannot be used.
        try:
            await member.send(embed=welcome_embed)
        except discord.Forbidden:
            logger.info("Could not send welcome to channel or DM to %s (DMs closed)", member.id)
        except discord.HTTPException:
            logger.exception("Failed sending welcome DM to %s", member.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return
        try:
            settings = await self.db.get_guild_settings(member.guild.id)
        except Exception:
            logger.exception("Failed to load guild settings for member leave %s", member.guild.id)
            settings = None
        if settings:
            await send_log_embed(
                member.guild,
                settings,
                "Member left",
                f"{member.mention} left the server.",
                discord.Color.light_grey(),
            )
        try:
            result = await self.db.purge_user_data(member.guild.id, member.id)
        except Exception:
            logger.exception("Failed to purge DB data on member leave for %s", member.id)
            return
        if (
            result.applications_deleted
            or result.pending_deleted
            or result.recruiter_claims_cleared
        ):
            logger.info(
                "Member leave purge user=%s guild=%s apps=%s pending=%s recruiter_refs=%s",
                member.id,
                member.guild.id,
                result.applications_deleted,
                result.pending_deleted,
                result.recruiter_claims_cleared,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Onboarding(bot))
