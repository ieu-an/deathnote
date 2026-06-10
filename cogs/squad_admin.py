"""
Admin slash commands for squad configuration.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.checks import ensure_admin_or_botmod
from utils.database import Database
from utils.squads import SquadWeights, normalize_squad_key

logger = logging.getLogger(__name__)


class SquadEditModal(discord.ui.Modal, title="Edit squad"):
    def __init__(self, db: Database, squad_id: int, *, name: str, description: str, emoji: str, welcome: str) -> None:
        super().__init__()
        self._db = db
        self._squad_id = squad_id
        self._name = discord.ui.TextInput(label="Name", default=name[:100], max_length=100, required=True)
        self._description = discord.ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            default=description[:2000],
            max_length=2000,
            required=False,
        )
        self._emoji = discord.ui.TextInput(label="Emoji", default=emoji[:16], max_length=16, required=False)
        self._welcome = discord.ui.TextInput(
            label="Welcome message (on accept)",
            style=discord.TextStyle.paragraph,
            default=welcome[:2000],
            max_length=2000,
            required=False,
        )
        for item in (self._name, self._description, self._emoji, self._welcome):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._db.update_squad(
            self._squad_id,
            name=str(self._name.value).strip(),
            description=str(self._description.value).strip(),
            emoji=str(self._emoji.value).strip(),
            welcome_message=str(self._welcome.value).strip(),
        )
        await interaction.response.send_message(
            embed=discord.Embed(title="Squad updated", description="Changes saved.", color=discord.Color.green()),
            ephemeral=True,
        )


class SquadAdmin(commands.Cog):
    """Squad management under ``/admin``."""

    admin = app_commands.Group(name="admin", description="Administrative squad configuration")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @admin.command(name="squads", description="List configured recruitment squads.")
    async def admin_squads(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        squads = await self.db.list_squads(interaction.guild.id)
        if not squads:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Squads",
                    description="No squads configured. Use `/admin squad add` to create one.",
                    color=discord.Color.orange(),
                ),
                ephemeral=True,
            )
            return

        lines = []
        for s in squads:
            games = ", ".join(s.games) if s.games else "—"
            lines.append(
                f"**{s.display_name}** (`{s.key}`)\n"
                f"Games: {games}\n"
                f"Role: {f'<@&{s.role_id}>' if s.role_id else '—'}"
            )
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Configured squads",
                description="\n\n".join(lines),
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    squad_group = app_commands.Group(name="squad", description="Manage a single squad", parent=admin)

    @squad_group.command(name="add", description="Add a new recruitment squad.")
    @app_commands.describe(
        key="Unique slug (e.g. deathnote)",
        name="Display name",
        description="Squad description shown to recruits",
        emoji="Optional emoji prefix",
        role="Recruit role assigned on accept",
        leader_role="Optional leader role (stored for future use)",
        channel="Optional staff channel for this squad's applications",
        recruiter_role="Optional recruiter role pinged on new applications",
    )
    async def squad_add(
        self,
        interaction: discord.Interaction,
        key: str,
        name: str,
        description: str = "",
        emoji: str = "",
        role: Optional[discord.Role] = None,
        leader_role: Optional[discord.Role] = None,
        channel: Optional[discord.TextChannel] = None,
        recruiter_role: Optional[discord.Role] = None,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        norm_key = normalize_squad_key(key)
        if not norm_key:
            await interaction.response.send_message(
                embed=discord.Embed(title="Invalid key", description="Provide a valid squad key.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        existing = await self.db.get_squad_by_key(interaction.guild.id, norm_key)
        if existing:
            await interaction.response.send_message(
                embed=discord.Embed(title="Already exists", description=f"Squad `{norm_key}` already exists.", color=discord.Color.orange()),
                ephemeral=True,
            )
            return

        squad = await self.db.insert_squad(
            interaction.guild.id,
            key=norm_key,
            name=name.strip(),
            description=description.strip(),
            emoji=emoji.strip(),
            role_id=role.id if role else None,
            leader_role_id=leader_role.id if leader_role else None,
            channel_id=channel.id if channel else None,
            recruiter_role_id=recruiter_role.id if recruiter_role else None,
        )
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Squad added",
                description=f"Created **{squad.display_name}** (`{squad.key}`).",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )
        logger.info("Squad added key=%s by %s", norm_key, interaction.user.id)

    @squad_group.command(name="edit", description="Edit squad name, description, emoji, or welcome message.")
    @app_commands.describe(key="Squad key to edit")
    async def squad_edit(self, interaction: discord.Interaction, key: str) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        squad = await self.db.get_squad_by_key(interaction.guild.id, key)
        if not squad:
            await interaction.response.send_message(
                embed=discord.Embed(title="Not found", description=f"No squad `{key}`.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            SquadEditModal(
                self.db,
                squad.id,
                name=squad.name,
                description=squad.description,
                emoji=squad.emoji,
                welcome=squad.welcome_message,
            )
        )

    @squad_group.command(name="remove", description="Remove a squad configuration.")
    @app_commands.describe(key="Squad key to remove")
    async def squad_remove(self, interaction: discord.Interaction, key: str) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        deleted = await self.db.delete_squad(interaction.guild.id, key)
        if not deleted:
            await interaction.response.send_message(
                embed=discord.Embed(title="Not found", description=f"No squad `{key}`.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=discord.Embed(title="Squad removed", description=f"Removed `{key}`.", color=discord.Color.green()),
            ephemeral=True,
        )
        logger.info("Squad removed key=%s by %s", key, interaction.user.id)

    @squad_group.command(name="games", description="Set the games list used for recommendations.")
    @app_commands.describe(key="Squad key", games="Comma-separated game names")
    async def squad_games(self, interaction: discord.Interaction, key: str, games: str) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        squad = await self.db.get_squad_by_key(interaction.guild.id, key)
        if not squad:
            await interaction.response.send_message(
                embed=discord.Embed(title="Not found", description=f"No squad `{key}`.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        game_list = [g.strip() for g in games.split(",") if g.strip()]
        await self.db.update_squad(squad.id, games_json=game_list)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Games updated",
                description=f"**{squad.name}** games:\n" + (", ".join(game_list) or "—"),
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    @squad_group.command(name="weights", description="Configure recommendation scoring weights for a squad.")
    @app_commands.describe(
        key="Squad key",
        game_match="Points per matching game (default 3)",
        most_played_match="Points when most-played game matches (default 5)",
        genre_match="Points when preferred genre matches (default 2)",
    )
    async def squad_weights(
        self,
        interaction: discord.Interaction,
        key: str,
        game_match: Optional[int] = None,
        most_played_match: Optional[int] = None,
        genre_match: Optional[int] = None,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        squad = await self.db.get_squad_by_key(interaction.guild.id, key)
        if not squad:
            await interaction.response.send_message(
                embed=discord.Embed(title="Not found", description=f"No squad `{key}`.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        weights = squad.weights.to_dict()
        if game_match is not None:
            weights["game_match"] = game_match
        if most_played_match is not None:
            weights["most_played_match"] = most_played_match
        if genre_match is not None:
            weights["genre_match"] = genre_match

        parsed = SquadWeights.from_dict(weights)
        await self.db.update_squad(squad.id, weights_json=parsed.to_dict())
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Weights updated",
                description=(
                    f"**{squad.name}** recommendation weights:\n"
                    f"• Game match: **{parsed.game_match}**\n"
                    f"• Most played: **{parsed.most_played_match}**\n"
                    f"• Genre match: **{parsed.genre_match}**"
                ),
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SquadAdmin(bot))
