"""
Applicant-facing squad selection: manual choice, questionnaire, and recommendation.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import discord
from discord.ext import commands

from utils.branding import BRAND_EMBED_COLOR, brand_user_embed
from utils.database import Database
from utils.recommendation import RecommendationResult, recommend
from utils.recruitment_embeds import send_log_embed
from utils.squads import SELECTION_MANUAL, SELECTION_RECOMMENDED, Squad, squad_by_key

logger = logging.getLogger(__name__)

GENRE_OPTIONS = [
    discord.SelectOption(label="FPS", value="FPS"),
    discord.SelectOption(label="RPG", value="RPG"),
    discord.SelectOption(label="MMO", value="MMO"),
    discord.SelectOption(label="Racing", value="Racing"),
    discord.SelectOption(label="Sports", value="Sports"),
    discord.SelectOption(label="Survival", value="Survival"),
    discord.SelectOption(label="Strategy", value="Strategy"),
    discord.SelectOption(label="Mixed", value="Mixed"),
]

FREQUENCY_OPTIONS = [
    discord.SelectOption(label="Daily", value="Daily"),
    discord.SelectOption(label="Several times weekly", value="Several times weekly"),
    discord.SelectOption(label="Weekly", value="Weekly"),
    discord.SelectOption(label="Casual", value="Casual"),
]


def _collect_all_games(squads: list[Squad]) -> list[str]:
    seen: set[str] = set()
    games: list[str] = []
    for squad in squads:
        for game in squad.games:
            norm = game.strip().lower()
            if norm and norm not in seen:
                seen.add(norm)
                games.append(game.strip())
    return sorted(games, key=str.lower)


def build_squad_showcase_embed(squad: Squad) -> discord.Embed:
    embed = discord.Embed(
        title=squad.display_name,
        description=squad.description or "No description configured.",
        color=BRAND_EMBED_COLOR,
    )
    embed.add_field(name="Main Games", value=squad.games_display(), inline=False)
    if squad.genres:
        embed.add_field(name="Genres", value=", ".join(squad.genres), inline=False)
    return embed


class SquadContext:
    """Carries squad selection state into the verification modal."""

    def __init__(
        self,
        *,
        selected_squad_key: str,
        recommended_squad_key: str = "",
        selection_method: str = SELECTION_MANUAL,
        questionnaire: Optional[dict[str, Any]] = None,
        squad_scores: Optional[dict[str, int]] = None,
        confidence: str = "",
        recommendation_reasons: Optional[tuple[str, ...]] = None,
    ) -> None:
        self.selected_squad_key = selected_squad_key
        self.recommended_squad_key = recommended_squad_key
        self.selection_method = selection_method
        self.questionnaire = questionnaire or {}
        self.squad_scores = squad_scores or {}
        self.confidence = confidence
        self.recommendation_reasons = recommendation_reasons or ()


async def start_squad_selection(
    interaction: discord.Interaction,
    db: Database,
    squads: list[Squad],
) -> None:
    """Entry point from VerificationView when squads are configured."""
    embed = brand_user_embed(
        title="Choose Your Squad",
        description=(
            "Welcome! This recruitment hub serves multiple squads.\n\n"
            "**How would you like to choose your squad?**"
        ),
    )
    view = SquadMethodView(squads)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def _log_squad_action(interaction: discord.Interaction, message: str) -> None:
    if not interaction.guild:
        return
    bot = interaction.client
    db: Database = bot.db  # type: ignore[attr-defined]
    try:
        settings = await db.get_guild_settings(interaction.guild.id)
        await send_log_embed(
            interaction.guild,
            settings,
            "Squad selection",
            f"{interaction.user.mention}: {message}",
            discord.Color.blurple(),
        )
    except Exception:
        logger.exception("Failed to log squad action")


async def _open_verification_modal(
    interaction: discord.Interaction,
    squads: list[Squad],
    ctx: SquadContext,
    *,
    log_message: str,
) -> None:
    from cogs.verification import VerificationModal

    await _log_squad_action(interaction, log_message)
    await interaction.response.send_modal(VerificationModal(squad_context=ctx, squads=squads))


class SquadMethodView(discord.ui.View):
    def __init__(self, squads: list[Squad]) -> None:
        super().__init__(timeout=600)
        self.squads = squads

    @discord.ui.button(label="Choose myself", style=discord.ButtonStyle.primary)
    async def choose_myself(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._show_manual_selection(interaction)

    @discord.ui.button(label="Recommend one for me", style=discord.ButtonStyle.secondary)
    async def recommend_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        games = _collect_all_games(self.squads)
        if not games:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Questionnaire unavailable",
                    description="No games are configured for squads yet. Please choose a squad manually.",
                    color=discord.Color.orange(),
                ),
                ephemeral=True,
            )
            await self._show_manual_selection(interaction, use_followup=True)
            return

        embed = brand_user_embed(
            title="Gaming Questionnaire",
            description="**What games do you currently play?**\nSelect all that apply.",
        )
        view = QuestionnaireStep1View(self.squads, games)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _show_manual_selection(
        self,
        interaction: discord.Interaction,
        *,
        use_followup: bool = False,
    ) -> None:
        embeds = [build_squad_showcase_embed(s) for s in self.squads]
        view = ManualSquadSelectView(self.squads)
        content = "Browse the squads below and click **Join** on the one you want."
        if use_followup:
            await interaction.followup.send(content=content, embeds=embeds[:10], view=view, ephemeral=True)
        else:
            await interaction.response.edit_message(content=content, embeds=embeds[:10], view=view)


class ManualSquadSelectView(discord.ui.View):
    def __init__(self, squads: list[Squad]) -> None:
        super().__init__(timeout=600)
        self.squads = squads
        for squad in squads[:5]:
            button = discord.ui.Button(
                label=f"Join {squad.name}"[:80],
                style=discord.ButtonStyle.success,
                emoji=squad.emoji or None,
            )
            button.callback = self._make_join_callback(squad)  # type: ignore[method-assign]
            self.add_item(button)

    def _make_join_callback(self, squad: Squad):
        async def callback(interaction: discord.Interaction) -> None:
            ctx = SquadContext(selected_squad_key=squad.key, selection_method=SELECTION_MANUAL)
            await _open_verification_modal(
                interaction,
                self.squads,
                ctx,
                log_message=f"Recruit selected **{squad.name}** manually.",
            )

        return callback


class QuestionnaireStep1View(discord.ui.View):
    """Multi-select: games currently played."""

    def __init__(self, squads: list[Squad], games: list[str]) -> None:
        super().__init__(timeout=600)
        self.squads = squads
        self.answers: dict[str, Any] = {}
        options = [discord.SelectOption(label=g[:100], value=g[:100]) for g in games[:25]]
        select = discord.ui.Select(
            placeholder="Select games you play…",
            min_values=1,
            max_values=min(len(options), 25),
            options=options,
        )
        select.callback = self._on_select  # type: ignore[method-assign]
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        select_vals = interaction.data.get("values", []) if interaction.data else []
        self.answers["games_played"] = list(select_vals)
        embed = brand_user_embed(
            title="Gaming Questionnaire",
            description="**Which game do you play most?**",
        )
        games = self.answers["games_played"]
        options = [discord.SelectOption(label=g[:100], value=g[:100]) for g in games]
        view = QuestionnaireStep2View(self.squads, self.answers, options)
        await interaction.response.edit_message(embed=embed, view=view)


class QuestionnaireStep2View(discord.ui.View):
    def __init__(
        self,
        squads: list[Squad],
        answers: dict[str, Any],
        options: list[discord.SelectOption],
    ) -> None:
        super().__init__(timeout=600)
        self.squads = squads
        self.answers = answers
        select = discord.ui.Select(
            placeholder="Select your most played game…",
            min_values=1,
            max_values=1,
            options=options,
        )
        select.callback = self._on_select  # type: ignore[method-assign]
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values", []) if interaction.data else []
        if values:
            self.answers["most_played"] = values[0]
        embed = brand_user_embed(
            title="Gaming Questionnaire",
            description="**Which genre do you prefer?**",
        )
        view = QuestionnaireStep3View(self.squads, self.answers)
        await interaction.response.edit_message(embed=embed, view=view)


class QuestionnaireStep3View(discord.ui.View):
    def __init__(self, squads: list[Squad], answers: dict[str, Any]) -> None:
        super().__init__(timeout=600)
        self.squads = squads
        self.answers = answers
        select = discord.ui.Select(
            placeholder="Select preferred genre…",
            min_values=1,
            max_values=1,
            options=GENRE_OPTIONS,
        )
        select.callback = self._on_select  # type: ignore[method-assign]
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values", []) if interaction.data else []
        if values:
            self.answers["preferred_genre"] = values[0]
        embed = brand_user_embed(
            title="Gaming Questionnaire",
            description="**How often do you play?**",
        )
        view = QuestionnaireStep4View(self.squads, self.answers)
        await interaction.response.edit_message(embed=embed, view=view)


class QuestionnaireStep4View(discord.ui.View):
    def __init__(self, squads: list[Squad], answers: dict[str, Any]) -> None:
        super().__init__(timeout=600)
        self.squads = squads
        self.answers = answers
        select = discord.ui.Select(
            placeholder="Select play frequency…",
            min_values=1,
            max_values=1,
            options=FREQUENCY_OPTIONS,
        )
        select.callback = self._on_select  # type: ignore[method-assign]
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values", []) if interaction.data else []
        if values:
            self.answers["play_frequency"] = values[0]

        result = recommend(self.squads, self.answers)
        best = squad_by_key(self.squads, result.best_squad_key)
        if not best:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Could not recommend",
                    description="Please choose a squad manually.",
                    color=discord.Color.orange(),
                ),
                ephemeral=True,
            )
            return

        reason = "\n".join(result.reasons)
        embed = brand_user_embed(
            title="Squad Recommendation",
            description=(
                f"**Recommended Squad:** {best.display_name}\n\n"
                f"**Reason:**\n{reason}\n\n"
                f"**Confidence:** {result.confidence}"
            ),
        )
        view = RecommendationConfirmView(self.squads, self.answers, result)
        await _log_squad_action(
            interaction,
            f"Recommendation engine suggested **{best.name}** (confidence: {result.confidence}).",
        )
        await interaction.response.edit_message(embed=embed, view=view)


class RecommendationConfirmView(discord.ui.View):
    def __init__(
        self,
        squads: list[Squad],
        answers: dict[str, Any],
        result: RecommendationResult,
    ) -> None:
        super().__init__(timeout=600)
        self.squads = squads
        self.answers = answers
        self.result = result
        best = squad_by_key(squads, result.best_squad_key)
        if best:
            join_btn = discord.ui.Button(
                label=f"Join {best.name}"[:80],
                style=discord.ButtonStyle.success,
                emoji=best.emoji or None,
            )
            join_btn.callback = self._make_accept_callback(best)  # type: ignore[method-assign]
            self.add_item(join_btn)

        diff_btn = discord.ui.Button(
            label="Choose Different Squad",
            style=discord.ButtonStyle.secondary,
        )
        diff_btn.callback = self._on_choose_different  # type: ignore[method-assign]
        self.add_item(diff_btn)

    def _make_accept_callback(self, squad: Squad):
        async def callback(interaction: discord.Interaction) -> None:
            ctx = SquadContext(
                selected_squad_key=squad.key,
                recommended_squad_key=squad.key,
                selection_method=SELECTION_RECOMMENDED,
                questionnaire=dict(self.answers),
                squad_scores=dict(self.result.scores),
                confidence=self.result.confidence,
                recommendation_reasons=self.result.reasons,
            )
            await _open_verification_modal(
                interaction,
                self.squads,
                ctx,
                log_message=f"Recruit accepted recommendation for **{squad.name}**.",
            )

        return callback

    async def _on_choose_different(self, interaction: discord.Interaction) -> None:
        await _log_squad_action(interaction, "Recruit chose to override recommendation.")
        embeds = [build_squad_showcase_embed(s) for s in self.squads]
        # Preserve questionnaire for manual override — attach to manual join via wrapper view
        view = ManualSquadSelectWithQuestionnaireView(
            self.squads,
            self.answers,
            self.result,
        )
        await interaction.response.edit_message(
            content="Browse the squads below and click **Join** on the one you want.",
            embeds=embeds[:10],
            view=view,
        )


class ManualSquadSelectWithQuestionnaireView(ManualSquadSelectView):
    """Manual selection after overriding a recommendation."""

    def __init__(
        self,
        squads: list[Squad],
        questionnaire: dict[str, Any],
        result: RecommendationResult,
    ) -> None:
        super().__init__(squads)
        self.questionnaire = questionnaire
        self.result = result
        # Replace join callbacks to include override context
        self.clear_items()
        for squad in squads[:5]:
            button = discord.ui.Button(
                label=f"Join {squad.name}"[:80],
                style=discord.ButtonStyle.success,
                emoji=squad.emoji or None,
            )
            button.callback = self._make_override_callback(squad)  # type: ignore[method-assign]
            self.add_item(button)

    def _make_override_callback(self, squad: Squad):
        async def callback(interaction: discord.Interaction) -> None:
            recommended_key = self.result.best_squad_key
            override = squad.key != recommended_key
            ctx = SquadContext(
                selected_squad_key=squad.key,
                recommended_squad_key=recommended_key,
                selection_method=SELECTION_MANUAL if override else SELECTION_RECOMMENDED,
                questionnaire=dict(self.questionnaire),
                squad_scores=dict(self.result.scores),
                confidence=self.result.confidence,
                recommendation_reasons=self.result.reasons,
            )
            msg = (
                f"Recruit overrode recommendation and selected **{squad.name}**."
                if override
                else f"Recruit selected **{squad.name}** manually."
            )
            await _open_verification_modal(interaction, self.squads, ctx, log_message=msg)

        return callback


class SquadSelectionCog(commands.Cog):
    """Placeholder cog for squad selection module."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SquadSelectionCog(bot))
