"""
Staff self-service: branded embed + buttons to add/remove ping / recruitment roles.

Channel: set ``STAFF_ROLE_PANEL_CHANNEL_ID`` in ``.env``, or hardcode
``HARDCODED_STAFF_ROLE_PANEL_CHANNEL_ID`` below. The bot stores the posted
message id in the database so restarts refresh the same message.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import discord
from discord.ext import commands

from utils.branding import BRAND_EMBED_COLOR, BRAND_THUMBNAIL_URL
from utils.database import Database, GuildSettings

logger = logging.getLogger(__name__)

# Optional: set an integer channel ID here (takes precedence over env).
HARDCODED_STAFF_ROLE_PANEL_CHANNEL_ID: Optional[int] = 1490612985146642482

TOGGLE_CUSTOM_ID_PREFIX = "staff_role_toggle_v1"


def _env_panel_channel_id() -> Optional[int]:
    raw = os.getenv("STAFF_ROLE_PANEL_CHANNEL_ID")
    if not raw:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("STAFF_ROLE_PANEL_CHANNEL_ID is not a valid integer; ignoring.")
        return None


def _allowed_panel_role_ids(settings: GuildSettings) -> set[int]:
    ids: set[int] = set()
    for rid in (settings.ping_role_id, settings.night_ping_role_id, settings.staff_role_id):
        if rid is not None:
            ids.add(rid)
    return ids


def _panel_role_entries(
    guild: discord.Guild,
    settings: GuildSettings,
) -> list[tuple[str, discord.Role]]:
    """Human-readable line + role for each configured panel role (deduped)."""
    seen: set[int] = set()
    out: list[tuple[str, discord.Role]] = []
    specs: list[tuple[Optional[int], str]] = [
        (settings.ping_role_id, "Staff ping — during the ping window"),
        (settings.night_ping_role_id, "After-hours ping — outside the main window"),
        (settings.staff_role_id, "Recruitment — applications & interviews"),
    ]
    for rid, desc in specs:
        if rid is None or rid in seen:
            continue
        role = guild.get_role(rid)
        if role is None:
            continue
        seen.add(rid)
        out.append((desc, role))
    return out


def _build_panel_embed(entries: list[tuple[str, discord.Role]]) -> discord.Embed:
    embed = discord.Embed(
        title="Staff roles",
        description=(
            "Use the buttons below to **add** or **remove** roles on yourself. "
            "You are only pinged for applications when you hold the matching ping role."
        ),
        color=BRAND_EMBED_COLOR,
    )
    embed.set_thumbnail(url=BRAND_THUMBNAIL_URL)
    if entries:
        lines = [f"**{desc}**\n{role.mention}" for desc, role in entries]
        embed.add_field(
            name="Roles on this panel",
            value="\n\n".join(lines)[:1024],
            inline=False,
        )
    else:
        embed.add_field(
            name="Nothing to show yet",
            value=(
                "Configure at least one of: `/setpingrole`, `/setnightpingrole`, or `/setstaffrole`, "
                "then restart the bot (or wait for the next panel refresh)."
            ),
            inline=False,
        )
    embed.set_footer(text="Deathnote · Staff role panel")
    return embed


class StaffRoleToggleButton(discord.ui.Button):
    """Persistent toggle; ``custom_id`` encodes the role snowflake."""

    def __init__(self, role_id: int, label: str) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=label[:80],
            custom_id=f"{TOGGLE_CUSTOM_ID_PREFIX}:{role_id}",
        )
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This can only be used in a server.",
                ephemeral=True,
            )
            return

        bot = interaction.client
        db: Database = bot.db  # type: ignore[attr-defined]
        settings = await db.get_guild_settings(interaction.guild.id)

        if self.role_id not in _allowed_panel_role_ids(settings):
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Unavailable",
                    description="This role is no longer on the staff panel.",
                    color=discord.Color.orange(),
                ),
                ephemeral=True,
            )
            return

        role = interaction.guild.get_role(self.role_id)
        if role is None:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Missing role",
                    description="That role no longer exists in this server.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        me = interaction.guild.me
        if me is not None and role >= me.top_role:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Cannot modify",
                    description="That role is above or equal to my top role. Ask an admin to move the bot role higher.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        member = interaction.user
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Staff role panel (self-service)")
                verb = "Removed"
            else:
                await member.add_roles(role, reason="Staff role panel (self-service)")
                verb = "Added"
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Permission denied",
                    description="I could not change your roles. Ensure I have **Manage Roles**.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Error",
                    description=f"Discord rejected the change: `{e}`",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=discord.Embed(
                title=verb,
                description=f"{verb} {role.mention}.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


def _build_panel_view(entries: list[tuple[str, discord.Role]]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for _desc, role in entries:
        view.add_item(StaffRoleToggleButton(role.id, role.name))
    return view


async def _sync_panel_in_channel(bot: commands.Bot, db: Database, channel: discord.TextChannel) -> None:
    """Post or edit the panel in ``channel`` and register persistent views."""
    guild = channel.guild
    settings = await db.get_guild_settings(guild.id)
    entries = _panel_role_entries(guild, settings)
    embed = _build_panel_embed(entries)
    view = _build_panel_view(entries) if entries else discord.ui.View(timeout=None)

    msg_id = settings.staff_role_panel_message_id
    message: Optional[discord.Message] = None

    if msg_id:
        try:
            message = await channel.fetch_message(msg_id)
        except discord.NotFound:
            message = None
        except discord.HTTPException as e:
            logger.exception("Staff role panel: fetch message %s failed: %s", msg_id, e)
            return

    try:
        if message:
            try:
                await message.edit(embed=embed, view=view)
            except discord.NotFound:
                message = await channel.send(embed=embed, view=view)
        else:
            message = await channel.send(embed=embed, view=view)
    except discord.Forbidden:
        logger.error(
            "Staff role panel: missing permissions to send/edit in %s (guild %s).",
            channel.mention,
            guild.id,
        )
        return
    except discord.HTTPException as e:
        logger.exception("Staff role panel: send/edit failed: %s", e)
        return

    await db.update_guild_settings(
        guild.id,
        staff_role_panel_channel_id=channel.id,
        staff_role_panel_message_id=message.id,
    )

    if view.children:
        try:
            bot.add_view(view, message_id=message.id)
        except ValueError:
            pass


async def sync_staff_role_panel(bot: commands.Bot) -> None:
    """Post or refresh staff role panel(s) and register persistent views."""
    db: Database = bot.db  # type: ignore[attr-defined]

    global_ch = HARDCODED_STAFF_ROLE_PANEL_CHANNEL_ID
    if global_ch is None:
        global_ch = _env_panel_channel_id()

    if global_ch is not None:
        try:
            ch = await bot.fetch_channel(global_ch)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            logger.warning("Staff role panel: could not load channel %s: %s", global_ch, e)
            return
        if not isinstance(ch, discord.TextChannel):
            logger.warning("Staff role panel: channel %s is not a text channel.", global_ch)
            return
        await _sync_panel_in_channel(bot, db, ch)
        return

    for guild in bot.guilds:
        settings = await db.get_guild_settings(guild.id)
        cid = settings.staff_role_panel_channel_id
        if cid is None:
            continue
        channel = guild.get_channel(cid)
        if channel is None:
            try:
                channel = await bot.fetch_channel(cid)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                logger.warning("Staff role panel: could not load channel %s: %s", cid, e)
                continue
        if not isinstance(channel, discord.TextChannel):
            logger.warning("Staff role panel: channel %s is not a text channel; skipping.", cid)
            continue
        if channel.guild.id != guild.id:
            continue
        await _sync_panel_in_channel(bot, db, channel)


class StaffRolePanelCog(commands.Cog):
    """Ensures the staff role panel exists after startup."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await sync_staff_role_panel(self.bot)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StaffRolePanelCog(bot))
