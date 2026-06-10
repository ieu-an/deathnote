"""Optional role mentions for new applications: primary window vs after-hours (night) role."""

from __future__ import annotations

from typing import Optional, Tuple

import discord

from utils.database import GuildSettings
from utils.squads import Squad
from utils.time_utils import is_within_ping_window


def _effective_day_ping_role_id(settings: GuildSettings) -> Optional[int]:
    """Prefer ``ping_role_id``; if unset, fall back to ``staff_role_id`` (common setup mistake)."""
    if settings.ping_role_id is not None:
        return settings.ping_role_id
    return settings.staff_role_id


def staff_application_ping(settings: GuildSettings) -> Tuple[str, Optional[discord.AllowedMentions]]:
    """
    Return ``(content, allowed_mentions)`` for posting a new application in the staff channel.

    When ``ping_enabled``:

    * **Inside** the Eastern ``[ping_start, ping_end]`` window: mention the day ping role
      (``ping_role_id`` or ``staff_role_id``), if configured.
    * **Outside** that window: mention ``night_ping_role_id`` if set; otherwise no mention.

    When non-empty, ``allowed_mentions`` is set so Discord processes the role ping.
    """
    if not settings.ping_enabled:
        return "", None

    in_window = is_within_ping_window(settings.ping_start, settings.ping_end)
    if in_window:
        rid = _effective_day_ping_role_id(settings)
    else:
        rid = settings.night_ping_role_id

    if rid is None:
        return "", None

    mention = f"<@&{rid}>"
    return mention, discord.AllowedMentions(roles=[discord.Object(id=rid)])


def squad_recruiter_ping(squad: Optional[Squad]) -> str:
    """Return a recruiter role mention for the applicant's chosen squad, if configured."""
    if squad is None or squad.recruiter_role_id is None:
        return ""
    return f"<@&{squad.recruiter_role_id}>"


def staff_ping_suffix(settings: GuildSettings) -> str:
    """Role mention string for the current Eastern time window, or empty if none applies."""
    text, _ = staff_application_ping(settings)
    return text
