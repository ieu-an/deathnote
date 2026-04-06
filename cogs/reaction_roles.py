"""
Reaction-role panels: post an embed; members react to receive or remove roles.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.branding import BRAND_EMBED_COLOR, BRAND_THUMBNAIL_URL
from utils.checks import ensure_admin_or_botmod
from utils.database import Database

logger = logging.getLogger(__name__)

_CUSTOM_EMOJI = re.compile(r"^<a?:\w+:(\d+)>$")


def _emoji_storage_key_from_string(raw: str) -> str:
    """Normalize user input (Unicode or <:name:id>) to match raw reaction payloads."""
    s = raw.strip()
    m = _CUSTOM_EMOJI.match(s)
    if m:
        return m.group(1)
    return s


def _emoji_storage_key_from_partial(emoji: discord.PartialEmoji) -> str:
    if emoji.id is not None:
        return str(emoji.id)
    return emoji.name or ""


def _build_reaction_panel_embed(
    title: str,
    *,
    body: str,
    pairs: list[tuple[str, discord.Role]],
) -> discord.Embed:
    """Member-facing panel: brand color, thumbnail, and structured fields."""
    embed = discord.Embed(
        title=title[:256],
        description=body if body else None,
        color=BRAND_EMBED_COLOR,
    )
    embed.set_thumbnail(url=BRAND_THUMBNAIL_URL)
    role_lines = [f"{e}  →  {role.mention}" for e, role in pairs]
    embed.add_field(
        name="Choose your roles",
        value="\n".join(role_lines)[:1024],
        inline=False,
    )
    embed.set_footer(text="Deathnote · Reaction roles")
    return embed


class ReactionRoles(commands.Cog):
    """Admin-configured reaction → role mappings."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(
        name="reactionroleembed",
        description="Post a reaction-role embed in a channel (admin / bot moderator).",
    )
    @app_commands.describe(
        channel="Channel where the panel will be posted",
        description="Embed body text (explain what each reaction grants)",
        emoji1="First emoji — Unicode or <:name:id>",
        role1="First role",
        emoji2="Second emoji (optional)",
        role2="Second role (optional)",
        emoji3="Third emoji (optional)",
        role3="Third role (optional)",
        emoji4="Fourth emoji (optional)",
        role4="Fourth role (optional)",
        emoji5="Fifth emoji (optional)",
        role5="Fifth role (optional)",
        emoji6="Sixth emoji (optional)",
        role6="Sixth role (optional)",
        emoji7="Seventh emoji (optional)",
        role7="Seventh role (optional)",
        emoji8="Eighth emoji (optional)",
        role8="Eighth role (optional)",
        emoji9="Ninth emoji (optional)",
        role9="Ninth role (optional)",
        emoji10="Tenth emoji (optional)",
        role10="Tenth role (optional)",
        title="Embed title (optional)",
    )
    async def reaction_role_embed(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        description: str,
        emoji1: str,
        role1: discord.Role,
        emoji2: Optional[str] = None,
        role2: Optional[discord.Role] = None,
        emoji3: Optional[str] = None,
        role3: Optional[discord.Role] = None,
        emoji4: Optional[str] = None,
        role4: Optional[discord.Role] = None,
        emoji5: Optional[str] = None,
        role5: Optional[discord.Role] = None,
        emoji6: Optional[str] = None,
        role6: Optional[discord.Role] = None,
        emoji7: Optional[str] = None,
        role7: Optional[discord.Role] = None,
        emoji8: Optional[str] = None,
        role8: Optional[discord.Role] = None,
        emoji9: Optional[str] = None,
        role9: Optional[discord.Role] = None,
        emoji10: Optional[str] = None,
        role10: Optional[discord.Role] = None,
        title: Optional[str] = None,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        slots: list[tuple[Optional[str], Optional[discord.Role]]] = [
            (emoji1, role1),
            (emoji2, role2),
            (emoji3, role3),
            (emoji4, role4),
            (emoji5, role5),
            (emoji6, role6),
            (emoji7, role7),
            (emoji8, role8),
            (emoji9, role9),
            (emoji10, role10),
        ]

        pairs: list[tuple[str, discord.Role]] = []
        for e, r in slots:
            e_blank = e is None or not str(e).strip()
            if e_blank and r is None:
                continue
            if e_blank or r is None:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="Invalid pairs",
                        description="Each emoji must have a matching role (and vice versa). Leave unused pairs empty.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return
            pairs.append((str(e).strip(), r))

        if not pairs:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Nothing to add",
                    description="Provide at least one emoji and role.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        keys_seen: set[str] = set()
        roles_seen: set[int] = set()
        for e_str, role in pairs:
            key = _emoji_storage_key_from_string(e_str)
            if key in keys_seen:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="Duplicate emoji",
                        description="Each reaction must use a different emoji.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return
            keys_seen.add(key)
            if role.id in roles_seen:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="Duplicate role",
                        description="Each reaction must grant a different role.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return
            roles_seen.add(role.id)

        me = interaction.guild.me
        if me is None:
            await interaction.response.send_message(
                embed=discord.Embed(title="Error", description="Could not resolve bot member.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        for _e, role in pairs:
            if role.managed:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="Invalid role",
                        description=f"{role.mention} is a managed (integration) role and cannot be assigned.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return
            if role >= me.top_role:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="Role hierarchy",
                        description=(
                            f"The bot's top role must be **above** {role.mention} so it can assign it. "
                            "Move the bot role higher in Server Settings → Roles."
                        ),
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

        bot_perms = channel.permissions_for(me)
        if not bot_perms.send_messages or not bot_perms.embed_links or not bot_perms.add_reactions:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Missing permissions",
                    description=f"The bot needs **Send Messages**, **Embed Links**, and **Add Reactions** in {channel.mention}.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return
        if not interaction.guild.me.guild_permissions.manage_roles:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Missing permission",
                    description="The bot needs **Manage Roles** in this server.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        embed_title = (title or "").strip() or "Reaction Roles"
        body = description.strip()[:4000]
        embed = _build_reaction_panel_embed(embed_title, body=body, pairs=pairs)

        try:
            msg = await channel.send(embed=embed)
        except discord.HTTPException as e:
            logger.exception("reactionroleembed send failed: %s", e)
            await interaction.followup.send(
                embed=discord.Embed(
                    title="Could not post",
                    description=f"Failed to send the embed: `{e}`",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        db_pairs: list[tuple[str, int]] = []
        for e_str, role in pairs:
            key = _emoji_storage_key_from_string(e_str)
            db_pairs.append((key, role.id))
            try:
                await msg.add_reaction(e_str)
            except discord.HTTPException as e:
                logger.exception("add_reaction failed: %s", e)
                try:
                    await msg.delete()
                except discord.HTTPException:
                    pass
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="Invalid emoji",
                        description=(
                            f"Could not add reaction `{e_str}`: `{e}`\n"
                            "Use a standard emoji or an emoji from this server."
                        ),
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

        await self.db.add_reaction_role_bindings(
            interaction.guild.id,
            channel.id,
            msg.id,
            db_pairs,
        )

        await interaction.followup.send(
            embed=discord.Embed(
                title="Reaction panel posted",
                description=f"Posted in {channel.mention}.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle_reaction(payload, add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle_reaction(payload, add=False)

    async def _handle_reaction(self, payload: discord.RawReactionActionEvent, *, add: bool) -> None:
        if payload.user_id == self.bot.user.id:
            return
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        key = _emoji_storage_key_from_partial(payload.emoji)
        role_id = await self.db.get_reaction_role_for_emoji(guild.id, payload.message_id, key)
        if role_id is None:
            return

        role = guild.get_role(role_id)
        if role is None:
            return

        member: Optional[discord.Member] = payload.member
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.NotFound:
                return

        if member.bot:
            return

        me = guild.me
        if me is None or role >= me.top_role:
            return

        try:
            if add:
                await member.add_roles(role, reason="Reaction role panel")
            else:
                await member.remove_roles(role, reason="Reaction role panel")
        except discord.HTTPException:
            logger.exception("reaction role assign/remove failed user=%s role=%s", member.id, role_id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRoles(bot))
