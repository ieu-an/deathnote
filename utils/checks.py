"""
Permission checks for administrative commands and staff-only UI (buttons).

Administrators and manage-server users can use admin slash commands.
Optional ``botmod_role_id`` extends admin command access.

Staff actions (application buttons, claim) also allow ``staff_role_id`` and
``night_ping_role_id`` when set, plus administrator / manage server / botmod
(configurable overlap).
"""

from __future__ import annotations

import discord

from utils.database import GuildSettings


def member_can_use_admin_commands(member: discord.Member, settings: GuildSettings) -> bool:
    """True if the member may run admin/botmod slash commands for this guild."""
    if member.guild_permissions.administrator:
        return True
    if member.guild_permissions.manage_guild:
        return True
    if settings.botmod_role_id is not None:
        role = member.guild.get_role(settings.botmod_role_id)
        if role and role in member.roles:
            return True
    return False


def is_staff_member(member: discord.Member, settings: GuildSettings) -> bool:
    """
    True if the member may use staff recruitment controls (decision buttons, claim).

    Uses Administrator, Manage Server, optional ``staff_role_id``, optional
    ``night_ping_role_id`` (after-hours / night staff), and ``botmod_role_id``.
    """
    if member.guild_permissions.administrator:
        return True
    if member.guild_permissions.manage_guild:
        return True
    if settings.staff_role_id is not None:
        sr = member.guild.get_role(settings.staff_role_id)
        if sr and sr in member.roles:
            return True
    if settings.night_ping_role_id is not None:
        nr = member.guild.get_role(settings.night_ping_role_id)
        if nr and nr in member.roles:
            return True
    if settings.botmod_role_id is not None:
        br = member.guild.get_role(settings.botmod_role_id)
        if br and br in member.roles:
            return True
    return False


async def ensure_admin_or_botmod(interaction: discord.Interaction, settings: GuildSettings) -> bool:
    """Respond with an embed error if the user lacks admin/botmod permission; otherwise True."""
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Unavailable",
                description="This command can only be used inside a server.",
                color=discord.Color.red(),
            ),
            ephemeral=True,
        )
        return False

    if member_can_use_admin_commands(interaction.user, settings):
        return True

    await interaction.response.send_message(
        embed=discord.Embed(
            title="Permission denied",
            description="You need **Administrator**, **Manage Server**, or the configured bot moderator role.",
            color=discord.Color.red(),
        ),
        ephemeral=True,
    )
    return False


async def ensure_staff(interaction: discord.Interaction, settings: GuildSettings) -> bool:
    """Respond with an embed error if the user is not staff for recruitment actions."""
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Unavailable",
                description="This action can only be used inside a server.",
                color=discord.Color.red(),
            ),
            ephemeral=True,
        )
        return False

    if is_staff_member(interaction.user, settings):
        return True

    await interaction.response.send_message(
        embed=discord.Embed(
            title="Staff only",
            description="You do not have permission to use this control.",
            color=discord.Color.red(),
        ),
        ephemeral=True,
    )
    return False
