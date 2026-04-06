"""
Interview thread UI: recruiter claim button (persistent view).
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from utils.checks import ensure_staff
from utils.database import Database
from utils.recruitment_embeds import audit_log_embed

logger = logging.getLogger(__name__)

CLAIM_BUTTON_CUSTOM_ID = "recruit_claim_interview_v1"
INTERVIEW_CONCLUDE_ACCEPT_ID = "recruit_interview_conclude_accept_v1"
INTERVIEW_CONCLUDE_REJECT_ID = "recruit_interview_conclude_reject_v1"


class InterviewClaimView(discord.ui.View):
    """Persistent; lookup application by thread id on click."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Claim interview",
        style=discord.ButtonStyle.primary,
        emoji="🔵",
        custom_id=CLAIM_BUTTON_CUSTOM_ID,
    )
    async def claim_interview(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.channel, (discord.Thread, discord.TextChannel)):
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Error",
                    description="Use this inside the interview thread or interview channel.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        bot = interaction.client
        db: Database = bot.db  # type: ignore[attr-defined]
        settings = await db.get_guild_settings(interaction.guild.id)

        if not await ensure_staff(interaction, settings):
            return

        app = await db.get_application_by_thread_id(interaction.guild.id, interaction.channel.id)
        if not app:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Not found",
                    description="No application linked to this interview thread or channel.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        if app.recruiter_id is not None:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Already claimed",
                    description=f"<@{app.recruiter_id}> is already assigned.",
                    color=discord.Color.orange(),
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        recruiter_id = interaction.user.id
        await db.set_application_recruiter(app.id, recruiter_id)

        button.disabled = True
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            logger.exception("Failed to disable claim button")

        confirm = f"{interaction.user.mention} is now handling this interview."
        try:
            await interaction.channel.send(confirm)
        except discord.HTTPException:
            logger.exception("Failed to send claim confirmation")

        await interaction.followup.send(embed=discord.Embed(title="Claimed", description="You are the assigned recruiter.", color=discord.Color.green()), ephemeral=True)

        if settings.log_channel_id:
            log_ch = interaction.guild.get_channel(settings.log_channel_id)
            if isinstance(log_ch, discord.TextChannel):
                app_m = interaction.guild.get_member(app.user_id)
                applicant_mention = app_m.mention if app_m else f"<@{app.user_id}>"
                try:
                    await log_ch.send(
                        embed=audit_log_embed(
                            "Interview claimed",
                            (
                                f"Application `#{app.id}` — applicant {applicant_mention} — "
                                f"{interaction.user.mention} claimed in {interaction.channel.mention}"
                            ),
                            discord.Color.blue(),
                        )
                    )
                except discord.HTTPException:
                    logger.exception("Log post failed")

        conclusion = discord.Embed(
            title="Conclude interview",
            description=(
                "When the interview is finished, use the buttons below.\n"
                "**Only the assigned recruiter** (you) can accept or reject the applicant."
            ),
            color=discord.Color.blurple(),
        )
        conclusion.add_field(name="Applicant", value=f"<@{app.user_id}> (`{app.user_id}`)", inline=False)
        conclusion.add_field(name="Application", value=f"`#{app.id}`", inline=True)
        try:
            await interaction.channel.send(embed=conclusion, view=InterviewConclusionView())
        except discord.HTTPException:
            logger.exception("Failed to post interview conclusion panel")


class InterviewConclusionView(discord.ui.View):
    """Accept / reject after claim; only assigned recruiter may use (enforced in verification)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Accept applicant",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id=INTERVIEW_CONCLUDE_ACCEPT_ID,
    )
    async def conclude_accept(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        from cogs.verification import handle_interview_conclusion_accept

        await handle_interview_conclusion_accept(interaction)

    @discord.ui.button(
        label="Reject applicant",
        style=discord.ButtonStyle.danger,
        emoji="❌",
        custom_id=INTERVIEW_CONCLUDE_REJECT_ID,
    )
    async def conclude_reject(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        from cogs.verification import handle_interview_conclusion_reject

        await handle_interview_conclusion_reject(interaction)


class InterviewCog(commands.Cog):
    """Placeholder cog to attach interview-related future commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InterviewCog(bot))
    bot.add_view(InterviewClaimView())
    bot.add_view(InterviewConclusionView())
