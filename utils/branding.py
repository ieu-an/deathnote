"""
Shared Deathnote visuals for member-facing embeds (DMs and verification panel).
"""

from __future__ import annotations

from typing import Optional

import discord

BRAND_THUMBNAIL_URL = (
    "https://cdn.discordapp.com/attachments/1487687307317215262/1487687436547919873/Deathnote.png"
    "?ex=69ca0c64&is=69c8bae4&hm=ad69ead920dab4149a54ed2de70f8dd64724a4838b31abf9a7f65e597946c7af&"
)

# Dark red for recruitment / onboarding embeds shown to members
BRAND_EMBED_COLOR = discord.Color(0x8B0000)


def brand_user_embed(*, title: Optional[str] = None, description: str) -> discord.Embed:
    """Embed with brand thumbnail and dark red accent."""
    embed = discord.Embed(
        title=title,
        description=description,
        color=BRAND_EMBED_COLOR,
    )
    embed.set_thumbnail(url=BRAND_THUMBNAIL_URL)
    return embed
