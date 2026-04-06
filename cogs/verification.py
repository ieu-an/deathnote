"""
Verification: modal (5 fields), staff embed + decision buttons.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.interview import InterviewClaimView
from utils.checks import ensure_admin_or_botmod, ensure_staff
from utils.database import (
    STATUS_ACCEPTED,
    STATUS_DENIED,
    STATUS_INTERVIEW,
    STATUS_SUBMITTED,
    Application,
    Database,
)
from utils.branding import BRAND_EMBED_COLOR, BRAND_THUMBNAIL_URL, brand_user_embed
from utils.ping_helpers import staff_application_ping
from utils.recruitment_embeds import build_application_embed, parse_answers, send_log_embed
from utils.time_utils import EASTERN, format_eastern_timestamp

logger = logging.getLogger(__name__)

VERIFY_BUTTON_CUSTOM_ID = "recruitment_verify_start_v1"


def _sanitize_interview_channel_name(app_id: int, member: discord.Member) -> str:
    raw = f"interview-{app_id}-{member.name}".lower()
    raw = re.sub(r"[^a-z0-9-]+", "-", raw)
    raw = re.sub(r"-+", "-", raw).strip("-")
    if not raw:
        raw = f"interview-{app_id}"
    return raw[:100]


async def _remove_interview_role_if_present(
    guild: discord.Guild,
    member: discord.Member,
    settings,
    *,
    reason: str = "Interview concluded",
) -> None:
    if not settings.interview_role_id:
        return
    ir = guild.get_role(settings.interview_role_id)
    if ir and ir in member.roles:
        try:
            await member.remove_roles(ir, reason=reason)
        except discord.HTTPException:
            logger.exception("Could not remove interview role")


def _interview_text_channel_overwrites(
    guild: discord.Guild,
    applicant: discord.Member,
    settings,
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    """
    Private channel: @everyone cannot see; applicant + staff/botmod roles can.
    """
    o: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        applicant: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
            use_external_emojis=True,
        ),
    }
    if settings.staff_role_id:
        sr = guild.get_role(settings.staff_role_id)
        if sr:
            o[sr] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
            )
    if settings.botmod_role_id:
        br = guild.get_role(settings.botmod_role_id)
        if br:
            o[br] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )
    return o

RECRUIT_ACCEPT_ID = "recruit_accept_v1"
RECRUIT_DENY_ID = "recruit_deny_v1"
RECRUIT_INTERVIEW_ID = "recruit_interview_v1"


class VerificationModal(discord.ui.Modal, title="Verification"):
    """Five TextInputs (Discord max)."""

    gamertag = discord.ui.TextInput(
        label="What is your Gamertag?",
        style=discord.TextStyle.short,
        placeholder="Your in-game name should resemble your display-name.",
        required=True,
        max_length=100,
    )
    age = discord.ui.TextInput(
        label="How old are you?",
        style=discord.TextStyle.short,
        placeholder="e.g. 21",
        required=True,
        max_length=10,
    )
    timezone = discord.ui.TextInput(
        label="What is your Timezone?",
        style=discord.TextStyle.short,
        placeholder="e.g. EST, GMT+1",
        required=True,
        max_length=64,
    )
    found_us = discord.ui.TextInput(
        label="Do you have a working headset/mic?",
        style=discord.TextStyle.paragraph,
        placeholder="We require members of Deathnote to have a working headset/mic for voice chat.",
        required=True,
        max_length=100,
    )
    why_join = discord.ui.TextInput(
        label="Why do you want to join Deathnote?",
        style=discord.TextStyle.paragraph,
        placeholder="Please mention what games you play!",
        required=True,
        max_length=2000,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Error",
                    description="This must be used inside a server.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        bot = interaction.client
        db: Database = bot.db  # type: ignore[attr-defined]

        try:
            settings = await db.get_guild_settings(interaction.guild.id)
        except Exception:
            logger.exception("DB error in verification modal")
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Error",
                    description="Could not load server settings. Try again later.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        if await db.user_has_active_application(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Application already active",
                    description="You already have a pending application. Please wait for staff review.",
                    color=discord.Color.orange(),
                ),
                ephemeral=True,
            )
            return

        if not settings.staff_channel_id:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Not configured",
                    description="Staff review channel is not set. Ask an admin to run `/setstaffchannel`.",
                    color=discord.Color.orange(),
                ),
                ephemeral=True,
            )
            return

        answers = {
            "gamertag": str(self.gamertag.value),
            "age": str(self.age.value),
            "timezone": str(self.timezone.value),
            "microphone": str(self.found_us.value),
            "why_join": str(self.why_join.value),
        }

        staff_ch = interaction.guild.get_channel(settings.staff_channel_id)
        if not isinstance(staff_ch, discord.TextChannel):
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Invalid channel",
                    description="Staff review channel is missing or not a text channel. Ask an admin to run `/setstaffchannel`.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        app_id = await db.insert_application(
            interaction.guild.id,
            interaction.user.id,
            status=STATUS_SUBMITTED,
            division="",
            answers=answers,
        )
        app = await db.get_application_by_id(app_id)
        assert app is not None

        member = interaction.guild.get_member(interaction.user.id)
        user = interaction.client.get_user(interaction.user.id) or interaction.user

        status_line = "**Pending review** — staff will use the buttons below."
        embed = build_application_embed(
            app,
            member=member,
            user=user,
            status_line=status_line,
            color=discord.Color.blurple(),
        )

        ping_content, ping_allowed = staff_application_ping(settings)
        ping_content = ping_content.strip()
        view = StaffDecisionView()

        try:
            msg = await staff_ch.send(
                content=ping_content or None,
                embed=embed,
                view=view,
                allowed_mentions=ping_allowed,
            )
        except discord.HTTPException as e:
            logger.exception("Failed to post application: %s", e)
            await db.delete_application_by_id(app_id)
            await interaction.followup.send(
                embed=discord.Embed(title="Error", description="Could not post to the staff channel.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        await db.update_application_staff_message(app_id, staff_message_id=msg.id, staff_channel_id=staff_ch.id)

        applicant_mention = member.mention if member else interaction.user.mention
        await send_log_embed(
            interaction.guild,
            settings,
            "New application",
            f"{applicant_mention} submitted application `#{app_id}`.\n[Open staff application card]({msg.jump_url})",
            discord.Color.blurple(),
            title_url=msg.jump_url,
        )

        try:
            await interaction.user.send(
                embed=brand_user_embed(
                    title="Application Received",
                    description=(
                        "Your answers were sent to staff for review.\n\n"
                        "You will receive another message here when your application is **accepted**, "
                        "**denied**, or if staff need an **interview**."
                    ),
                )
            )
        except discord.HTTPException:
            pass

        await interaction.followup.send(
            embed=discord.Embed(
                title="Submitted",
                description="Your application was sent to staff for review.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )
        logger.info("Application %s submitted for user %s", app_id, interaction.user.id)


class StaffDecisionView(discord.ui.View):
    """Persistent staff controls on the application message."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅", custom_id=RECRUIT_ACCEPT_ID)
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._handle_decision(interaction, "accept")

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="❌", custom_id=RECRUIT_DENY_ID)
    async def deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._handle_decision(interaction, "deny")

    @discord.ui.button(label="Interview", style=discord.ButtonStyle.primary, emoji="🟡", custom_id=RECRUIT_INTERVIEW_ID)
    async def interview_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._handle_decision(interaction, "interview")

    async def _handle_decision(self, interaction: discord.Interaction, action: str) -> None:
        if not interaction.guild or not interaction.message:
            return

        bot = interaction.client
        db: Database = bot.db  # type: ignore[attr-defined]
        settings = await db.get_guild_settings(interaction.guild.id)

        if not await ensure_staff(interaction, settings):
            return

        app = await db.get_application_by_staff_message(interaction.guild.id, interaction.message.id)
        if not app or app.status != STATUS_SUBMITTED:
            await interaction.response.send_message(
                embed=discord.Embed(title="Unavailable", description="This application is no longer actionable.", color=discord.Color.orange()),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        staff = interaction.user
        assert isinstance(staff, discord.Member)

        if action == "accept":
            await _do_accept(interaction, app, settings, staff, self)
        elif action == "deny":
            await _do_deny(interaction, app, settings, staff, self)
        else:
            await _do_interview(interaction, app, settings, staff, self)


async def _disable_all(view: StaffDecisionView) -> None:
    for child in view.children:
        if isinstance(child, discord.ui.Button):
            child.disabled = True


async def _fetch_staff_application_message(guild: discord.Guild, app: Application) -> Optional[discord.Message]:
    if app.staff_channel_id is None or app.staff_message_id is None:
        return None
    ch = guild.get_channel(app.staff_channel_id)
    if not isinstance(ch, discord.TextChannel):
        return None
    try:
        return await ch.fetch_message(app.staff_message_id)
    except (discord.NotFound, discord.HTTPException):
        return None


async def _edit_staff_card_simple(
    *,
    staff_card_message: Optional[discord.Message],
    app: Application,
    member: Optional[discord.Member],
    user: Optional[discord.User],
    status_line: str,
    color: discord.Color,
) -> None:
    if not staff_card_message:
        return
    disabled = StaffDecisionView()
    await _disable_all(disabled)
    try:
        await staff_card_message.edit(
            embed=build_application_embed(
                app,
                member=member,
                user=user,
                status_line=status_line,
                color=color,
            ),
            view=disabled,
        )
    except discord.HTTPException:
        logger.exception("Failed to edit staff application message")


async def _apply_accept(
    interaction: discord.Interaction,
    app: Application,
    settings,
    staff: discord.Member,
    *,
    staff_card_message: Optional[discord.Message],
) -> None:
    db: Database = interaction.client.db  # type: ignore[attr-defined]
    guild = interaction.guild
    assert guild

    member = guild.get_member(app.user_id)
    if member is None:
        try:
            member = await guild.fetch_member(app.user_id)
        except discord.NotFound:
            await db.update_application_status(app.id, status=STATUS_DENIED)
            await _edit_staff_card_simple(
                staff_card_message=staff_card_message,
                app=await db.get_application_by_id(app.id) or app,
                member=None,
                user=interaction.client.get_user(app.user_id),
                status_line=f"**Closed** — member left server (attempted by {staff.mention})",
                color=discord.Color.dark_gray(),
            )
            await interaction.followup.send(embed=discord.Embed(title="Member left", description="User is not in the server.", color=discord.Color.orange()), ephemeral=True)
            return

    if settings.accepted_role_id:
        role = guild.get_role(settings.accepted_role_id)
        if role:
            try:
                await member.add_roles(role, reason=f"Recruitment accepted by {staff.id}")
            except discord.HTTPException:
                logger.exception("Could not add accepted role")

    if settings.unverified_role_id:
        ur = guild.get_role(settings.unverified_role_id)
        if ur and ur in member.roles:
            try:
                await member.remove_roles(ur, reason="Recruitment accepted")
            except discord.HTTPException:
                logger.exception("Could not remove unverified role")

    await _remove_interview_role_if_present(guild, member, settings, reason="Interview concluded — accepted")

    try:
        await member.send(
            embed=brand_user_embed(
                title="Application to Deathnote Accepted",
                description="You have been accepted. You can now access the server.\n\n"
                "Please check the server rules to ensure you are prepared for the new world",
            )
        )
    except discord.HTTPException:
        pass

    await db.update_application_status(app.id, status=STATUS_ACCEPTED)
    app2 = await db.get_application_by_id(app.id)
    assert app2 is not None

    status_line = f"**Accepted** by {staff.mention}"
    await _edit_staff_card_simple(
        staff_card_message=staff_card_message,
        app=app2,
        member=member,
        user=member,
        status_line=status_line,
        color=discord.Color.green(),
    )

    await interaction.followup.send(embed=discord.Embed(title="Accepted", description="Applicant processed.", color=discord.Color.green()), ephemeral=True)
    await send_log_embed(
        guild,
        settings,
        "Application accepted",
        f"Application `#{app.id}` — applicant {member.mention} — accepted by {staff.mention}",
        discord.Color.green(),
    )
    logger.info("Accept: app %s by staff %s", app.id, staff.id)


async def _apply_deny(
    interaction: discord.Interaction,
    app: Application,
    settings,
    staff: discord.Member,
    *,
    staff_card_message: Optional[discord.Message],
) -> None:
    db: Database = interaction.client.db  # type: ignore[attr-defined]
    guild = interaction.guild
    assert guild

    user = interaction.client.get_user(app.user_id)
    if user:
        try:
            await user.send(
                embed=brand_user_embed(
                    title="Application denied",
                    description="Your application was not approved.",
                )
            )
        except discord.HTTPException:
            pass

    deny_member = guild.get_member(app.user_id)
    if deny_member is None:
        try:
            deny_member = await guild.fetch_member(app.user_id)
        except discord.NotFound:
            deny_member = None
    if deny_member:
        await _remove_interview_role_if_present(guild, deny_member, settings, reason="Interview concluded — denied")

    try:
        await guild.ban(discord.Object(id=app.user_id), reason=f"Denied by {staff.id}", delete_message_seconds=0)
    except discord.HTTPException as e:
        logger.warning("Ban failed for %s: %s", app.user_id, e)

    await db.update_application_status(app.id, status=STATUS_DENIED)
    app2 = await db.get_application_by_id(app.id)
    assert app2 is not None

    status_line = f"**Denied** by {staff.mention}"
    await _edit_staff_card_simple(
        staff_card_message=staff_card_message,
        app=app2,
        member=guild.get_member(app.user_id),
        user=user,
        status_line=status_line,
        color=discord.Color.red(),
    )

    await interaction.followup.send(embed=discord.Embed(title="Denied", description="User banned per pipeline.", color=discord.Color.red()), ephemeral=True)
    applicant_mention = deny_member.mention if deny_member else f"<@{app.user_id}>"
    await send_log_embed(
        guild,
        settings,
        "Application denied",
        f"Application `#{app.id}` — applicant {applicant_mention} — denied by {staff.mention}",
        discord.Color.red(),
    )
    logger.info("Deny: app %s by staff %s", app.id, staff.id)


async def _do_accept(
    interaction: discord.Interaction,
    app: Application,
    settings,
    staff: discord.Member,
    _view: StaffDecisionView,
) -> None:
    assert interaction.message
    await _apply_accept(interaction, app, settings, staff, staff_card_message=interaction.message)


async def _do_deny(
    interaction: discord.Interaction,
    app: Application,
    settings,
    staff: discord.Member,
    _view: StaffDecisionView,
) -> None:
    assert interaction.message
    await _apply_deny(interaction, app, settings, staff, staff_card_message=interaction.message)


async def _finalize_interview_conclusion_message(interaction: discord.Interaction, *, accepted: bool, staff: discord.Member) -> None:
    if not interaction.message:
        return
    title = "Interview concluded — Accepted" if accepted else "Interview concluded — Rejected"
    color = discord.Color.green() if accepted else discord.Color.red()
    desc = f"Decision recorded by {staff.mention}."
    try:
        await interaction.message.edit(
            embed=discord.Embed(title=title, description=desc, color=color),
            view=None,
        )
    except discord.HTTPException:
        logger.exception("Failed to update interview conclusion message")


async def _remove_applicant_from_interview_location(
    interaction: discord.Interaction,
    app: Application,
) -> None:
    """Remove applicant access from the current interview location."""
    if not interaction.guild:
        return

    member = interaction.guild.get_member(app.user_id)
    if member is None:
        try:
            member = await interaction.guild.fetch_member(app.user_id)
        except discord.NotFound:
            return

    channel = interaction.channel
    if isinstance(channel, discord.Thread):
        try:
            await channel.remove_user(member)
        except discord.HTTPException:
            logger.exception("Failed to remove applicant from interview thread")
        return

    if isinstance(channel, discord.TextChannel):
        try:
            await channel.set_permissions(
                member,
                view_channel=False,
                send_messages=False,
                read_message_history=False,
                reason=f"Interview concluded for application {app.id}",
            )
        except discord.HTTPException:
            logger.exception("Failed to remove applicant from interview channel")


async def handle_interview_conclusion_accept(interaction: discord.Interaction) -> None:
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

    db: Database = interaction.client.db  # type: ignore[attr-defined]
    settings = await db.get_guild_settings(interaction.guild.id)
    app = await db.get_application_by_thread_id(interaction.guild.id, interaction.channel.id)
    if not app or app.status != STATUS_INTERVIEW:
        await interaction.response.send_message(
            embed=discord.Embed(title="Unavailable", description="This interview is no longer actionable.", color=discord.Color.orange()),
            ephemeral=True,
        )
        return
    if app.recruiter_id is None or interaction.user.id != app.recruiter_id:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Not your interview",
                description="Only the recruiter who claimed this interview can accept or reject.",
                color=discord.Color.red(),
            ),
            ephemeral=True,
        )
        return

    staff = interaction.user
    assert isinstance(staff, discord.Member)
    await interaction.response.defer(ephemeral=True)
    staff_card = await _fetch_staff_application_message(interaction.guild, app)
    await _apply_accept(interaction, app, settings, staff, staff_card_message=staff_card)
    await _finalize_interview_conclusion_message(interaction, accepted=True, staff=staff)
    await _remove_applicant_from_interview_location(interaction, app)


async def handle_interview_conclusion_reject(interaction: discord.Interaction) -> None:
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

    db: Database = interaction.client.db  # type: ignore[attr-defined]
    settings = await db.get_guild_settings(interaction.guild.id)
    app = await db.get_application_by_thread_id(interaction.guild.id, interaction.channel.id)
    if not app or app.status != STATUS_INTERVIEW:
        await interaction.response.send_message(
            embed=discord.Embed(title="Unavailable", description="This interview is no longer actionable.", color=discord.Color.orange()),
            ephemeral=True,
        )
        return
    if app.recruiter_id is None or interaction.user.id != app.recruiter_id:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Not your interview",
                description="Only the recruiter who claimed this interview can accept or reject.",
                color=discord.Color.red(),
            ),
            ephemeral=True,
        )
        return

    staff = interaction.user
    assert isinstance(staff, discord.Member)
    await interaction.response.defer(ephemeral=True)
    staff_card = await _fetch_staff_application_message(interaction.guild, app)
    await _apply_deny(interaction, app, settings, staff, staff_card_message=staff_card)
    await _finalize_interview_conclusion_message(interaction, accepted=False, staff=staff)
    await _remove_applicant_from_interview_location(interaction, app)


async def _do_interview(
    interaction: discord.Interaction,
    app: Application,
    settings,
    staff: discord.Member,
    view: StaffDecisionView,
) -> None:
    db: Database = interaction.client.db  # type: ignore[attr-defined]
    guild = interaction.guild
    assert guild and interaction.message

    member = guild.get_member(app.user_id)
    user = interaction.client.get_user(app.user_id)
    if member is None:
        try:
            member = await guild.fetch_member(app.user_id)
        except discord.NotFound:
            await interaction.followup.send(embed=discord.Embed(title="Member left", description="User not in server.", color=discord.Color.orange()), ephemeral=True)
            return

    if settings.interview_role_id:
        ir = guild.get_role(settings.interview_role_id)
        if ir:
            try:
                await member.add_roles(ir, reason=f"Interview required by {staff.id}")
            except discord.HTTPException:
                logger.exception("Interview role assign failed")

    ts = int(time.time())
    interview_ch: Optional[discord.TextChannel] = None
    thread: Optional[discord.Thread] = None
    location_mention: Optional[str] = None

    if settings.interview_category_id:
        cat = guild.get_channel(settings.interview_category_id)
        if not isinstance(cat, discord.CategoryChannel):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="Interview category missing",
                    description=(
                        "The configured interview category no longer exists. "
                        "Ask an admin to run `/setinterviewcategory` with a valid category."
                    ),
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return
        chan_name = _sanitize_interview_channel_name(app.id, member)
        overwrites = _interview_text_channel_overwrites(guild, member, settings)
        try:
            interview_ch = await guild.create_text_channel(
                name=chan_name,
                category=cat,
                overwrites=overwrites,
                reason=f"Interview for application {app.id}",
            )
        except discord.HTTPException as e:
            logger.exception("Interview channel creation failed: %s", e)
            await interaction.followup.send(
                embed=discord.Embed(
                    title="Could not create interview channel",
                    description=(
                        "Check that the bot has **Manage Channels** in that category and try again. "
                        f"Details: `{e}`"
                    ),
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return
        location_mention = interview_ch.mention
    else:
        try:
            thread = await interaction.message.create_thread(
                name=f"Interview - {member.display_name}"[:100],
                auto_archive_duration=1440,
                reason=f"Interview for application {app.id}",
            )
        except discord.HTTPException:
            logger.exception("Thread creation failed")
        if thread:
            location_mention = thread.mention

    loc_id = interview_ch.id if interview_ch else (thread.id if thread else None)
    await db.update_application_status(
        app.id,
        status=STATUS_INTERVIEW,
        interview_started_at=ts,
        thread_id=loc_id,
    )

    dm_desc = (
        "**Your presence requires further evaluation.**\n\n"
        "Please use the private interview area linked below to answer a few questions and prove yourself.\n\n"
        "If you have any questions, please ask in the interview channel."
    )
    if interview_ch:
        dm_desc = f"{dm_desc}\n\nYour interview channel: [Open channel]({interview_ch.jump_url})"
    elif thread:
        dm_desc = f"{dm_desc}\n\nYour interview thread: [Jump to thread]({thread.jump_url})"

    try:
        await member.send(
            embed=brand_user_embed(
                title="Interview Required",
                description=dm_desc,
            )
        )
    except discord.HTTPException:
        pass

    target = interview_ch or thread
    if target:
        try:
            await target.send(
                content=f"{member.mention} — interview space. Staff will coordinate here.",
                view=InterviewClaimView(),
            )
        except discord.HTTPException:
            logger.exception("Failed to post claim view in interview location")

    app2 = await db.get_application_by_id(app.id)
    assert app2 is not None

    await _disable_all(view)
    status_line = f"**Interview required** by {staff.mention}"
    try:
        await interaction.message.edit(
            embed=build_application_embed(
                app2,
                member=member,
                user=user or member,
                status_line=status_line,
                color=discord.Color.gold(),
            ),
            view=view,
        )
    except discord.HTTPException:
        logger.exception("Failed to edit application message")

    if interview_ch:
        follow_desc = f"Interview channel: [Open channel]({interview_ch.jump_url})"
    elif thread:
        follow_desc = f"Interview thread: [Jump to thread]({thread.jump_url})"
    else:
        follow_desc = "Interview space could not be created."
    await interaction.followup.send(
        embed=discord.Embed(title="Interview", description=follow_desc, color=discord.Color.gold()),
        ephemeral=True,
    )
    await send_log_embed(
        guild,
        settings,
        "Interview required",
        f"Application `#{app.id}` — applicant {member.mention} — interview required by {staff.mention}"
        + (f" — {location_mention}" if location_mention else ""),
        discord.Color.gold(),
    )
    logger.info("Interview: app %s by staff %s", app.id, staff.id)


class Verification(commands.Cog):
    """Slash command to post the verification panel."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(name="setup_verification", description="Post the verification panel in a channel.")
    @app_commands.describe(channel="Where to post the verification embed")
    async def setup_verification(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if not interaction.guild:
            return

        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        await self.db.update_guild_settings(
            interaction.guild.id,
            verification_channel_id=channel.id,
        )

        embed = discord.Embed(
            title="You’ve Crossed the Threshold Into A New World.",
            description=(
                "This world is not like the one you came from.\n"
                "Every name here carries weight. Every action… consequence.\n\n"
                "Before you proceed, you must prove yourself.\n\n"
                "Complete the verification by pressing the button below and take your first step into Deathnote."
            ),
            color=BRAND_EMBED_COLOR,
        )
        embed.set_thumbnail(url=BRAND_THUMBNAIL_URL)

        view = VerificationView()
        try:
            await channel.send(embed=embed, view=view)
        except discord.HTTPException as e:
            logger.exception("Failed to post verification panel: %s", e)
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Error",
                    description="Could not send the verification message to that channel.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=discord.Embed(
                title="Verification panel posted",
                description=f"Channel: {channel.mention}\nVerification channel ID saved for this server.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )
        logger.info("Verification panel set up in %s by %s", channel.id, interaction.user.id)


class VerificationView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Start Verification",
        style=discord.ButtonStyle.primary,
        custom_id=VERIFY_BUTTON_CUSTOM_ID,
    )
    async def start_verification(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(VerificationModal())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Verification(bot))
    bot.add_view(VerificationView())
    bot.add_view(StaffDecisionView())
