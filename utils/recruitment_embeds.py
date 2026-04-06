"""
Standard application embeds and optional log channel posts.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import discord

from utils.database import Application, GuildSettings
from utils.time_utils import EASTERN, format_eastern_timestamp

logger = logging.getLogger(__name__)


def parse_answers(app: Application) -> dict[str, Any]:
    try:
        return json.loads(app.answers_json or "{}")
    except json.JSONDecodeError:
        return {}


def build_application_embed(
    app: Application,
    *,
    member: Optional[discord.Member],
    user: Optional[discord.User],
    status_line: str,
    color: discord.Color,
) -> discord.Embed:
    """Build the canonical staff-facing application embed."""
    answers = parse_answers(app)
    created = user.created_at if user else None
    joined = member.joined_at if member else None
    avatar_url = (member or user).display_avatar.url if (member or user) else None

    embed = discord.Embed(
        title="Recruitment application",
        color=color,
        timestamp=datetime.fromtimestamp(app.submitted_at, tz=timezone.utc),
    )
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)

    uid = app.user_id
    mention = member.mention if member else f"<@{uid}>"
    embed.add_field(name="Member", value=f"{mention}\n`{uid}`", inline=False)
    embed.add_field(
        name="Account created",
        value=discord.utils.format_dt(created, style="F") if created else "Unknown",
        inline=True,
    )
    embed.add_field(
        name="Joined server",
        value=discord.utils.format_dt(joined, style="F") if joined else "Unknown",
        inline=True,
    )
    if (app.division or "").strip():
        embed.add_field(name="Division", value=app.division, inline=True)
    embed.add_field(name="Gamertag", value=str(answers.get("gamertag", "—")), inline=True)
    embed.add_field(name="Age", value=str(answers.get("age", "—")), inline=True)
    embed.add_field(name="Timezone", value=str(answers.get("timezone", "—")), inline=True)
    _mic = answers.get("microphone") or answers.get("found_us")
    embed.add_field(name="Headset / mic (VC)", value=str(_mic or "—"), inline=False)
    embed.add_field(name="Why do you want to join?", value=str(answers.get("why_join", "—")), inline=False)
    embed.add_field(name="Status", value=status_line, inline=False)
    sub_local = datetime.fromtimestamp(app.submitted_at, tz=timezone.utc).astimezone(EASTERN)
    embed.set_footer(text=f"Application #{app.id} · Submitted at {format_eastern_timestamp(sub_local)} (Eastern)")
    return embed


def audit_log_embed(
    title: str,
    description: str,
    color: discord.Color = discord.Color.dark_gray(),
    *,
    title_url: Optional[str] = None,
    at: Optional[datetime] = None,
) -> discord.Embed:
    """Embed for log channel posts with event time (Discord timestamp + Eastern footer)."""
    ts_utc = datetime.now(timezone.utc) if at is None else at
    if ts_utc.tzinfo is None:
        ts_utc = ts_utc.replace(tzinfo=timezone.utc)
    else:
        ts_utc = ts_utc.astimezone(timezone.utc)
    ts_eastern = ts_utc.astimezone(EASTERN)
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=ts_utc,
    )
    if title_url:
        embed.url = title_url
    embed.set_footer(text=f"{format_eastern_timestamp(ts_eastern)} (Eastern)")
    return embed


async def send_log_embed(
    guild: discord.Guild,
    settings: GuildSettings,
    title: str,
    description: str,
    color: discord.Color = discord.Color.dark_gray(),
    *,
    title_url: Optional[str] = None,
    at: Optional[datetime] = None,
) -> None:
    if not settings.log_channel_id:
        return
    ch = guild.get_channel(settings.log_channel_id)
    if not isinstance(ch, discord.abc.Messageable):
        return
    try:
        await ch.send(
            embed=audit_log_embed(
                title,
                description,
                color,
                title_url=title_url,
                at=at,
            )
        )
    except discord.HTTPException:
        logger.exception("Failed to send log to channel %s", settings.log_channel_id)
