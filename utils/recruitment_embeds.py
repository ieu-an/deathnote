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
from utils.recommendation import CONFIDENCE_HIGH, CONFIDENCE_LOW, CONFIDENCE_MEDIUM
from utils.squads import SELECTION_MANUAL, SELECTION_RECOMMENDED, Squad, squad_by_key
from utils.time_utils import EASTERN, format_eastern_timestamp

logger = logging.getLogger(__name__)


def parse_answers(app: Application) -> dict[str, Any]:
    try:
        return json.loads(app.answers_json or "{}")
    except json.JSONDecodeError:
        return {}


def parse_questionnaire(app: Application) -> dict[str, Any]:
    try:
        data = json.loads(app.questionnaire_json or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def parse_squad_scores(app: Application) -> dict[str, int]:
    try:
        data = json.loads(app.squad_scores_json or "{}")
        if not isinstance(data, dict):
            return {}
        return {str(k): int(v) for k, v in data.items()}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _squad_label(key: str, squads: Optional[list[Squad]]) -> str:
    if not key:
        return "—"
    if squads:
        squad = squad_by_key(squads, key)
        if squad:
            return squad.display_name
    return key


def _confidence_from_scores(scores: dict[str, int], chosen_key: str) -> str:
    if not scores or not chosen_key:
        return "—"
    sorted_scores = sorted(scores.values(), reverse=True)
    chosen = scores.get(chosen_key, 0)
    if not sorted_scores:
        return CONFIDENCE_LOW
    best = sorted_scores[0]
    second = sorted_scores[1] if len(sorted_scores) > 1 else 0
    if chosen < best:
        return CONFIDENCE_LOW
    gap = best - second
    if gap >= 4:
        return CONFIDENCE_HIGH
    if gap >= 2:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def _format_questionnaire(q: dict[str, Any]) -> str:
    if not q:
        return "—"
    lines: list[str] = []
    games = q.get("games_played")
    if games:
        lines.append(f"**Games played:** {', '.join(games)}")
    if q.get("most_played"):
        lines.append(f"**Most played:** {q['most_played']}")
    if q.get("preferred_genre"):
        lines.append(f"**Preferred genre:** {q['preferred_genre']}")
    if q.get("play_frequency"):
        lines.append(f"**Play frequency:** {q['play_frequency']}")
    return "\n".join(lines) if lines else "—"


def build_application_embed(
    app: Application,
    *,
    member: Optional[discord.Member],
    user: Optional[discord.User],
    status_line: str,
    color: discord.Color,
    squads: Optional[list[Squad]] = None,
) -> discord.Embed:
    """Build the canonical staff-facing application embed."""
    answers = parse_answers(app)
    questionnaire = parse_questionnaire(app)
    scores = parse_squad_scores(app)
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

    if app.selected_squad or app.recommended_squad or app.selection_method:
        method_label = "—"
        if app.selection_method == SELECTION_MANUAL:
            method_label = "Manual"
        elif app.selection_method == SELECTION_RECOMMENDED:
            method_label = "Recommended"
        embed.add_field(name="Selection method", value=method_label, inline=True)
        embed.add_field(
            name="Recommended squad",
            value=_squad_label(app.recommended_squad, squads),
            inline=True,
        )
        embed.add_field(
            name="Chosen squad",
            value=_squad_label(app.selected_squad, squads),
            inline=True,
        )
        if scores:
            score_lines = []
            for key, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
                label = _squad_label(key, squads)
                score_lines.append(f"{label}: **{score}**")
            embed.add_field(name="Match scores", value="\n".join(score_lines), inline=False)
            confidence = _confidence_from_scores(scores, app.selected_squad)
            embed.add_field(name="Confidence", value=confidence, inline=True)
        if questionnaire:
            embed.add_field(name="Questionnaire", value=_format_questionnaire(questionnaire), inline=False)

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
    if (app.division or "").strip() and not app.selected_squad:
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
