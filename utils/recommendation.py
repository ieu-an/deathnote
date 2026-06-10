"""
Squad recommendation engine based on gaming questionnaire answers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from utils.squads import Squad, SquadWeights

CONFIDENCE_HIGH = "High"
CONFIDENCE_MEDIUM = "Medium"
CONFIDENCE_LOW = "Low"

GENRE_ALIASES: dict[str, frozenset[str]] = {
    "fps": frozenset({"fps", "first-person shooter", "shooter", "tactical shooter"}),
    "rpg": frozenset({"rpg", "role-playing"}),
    "mmo": frozenset({"mmo", "massively multiplayer"}),
    "racing": frozenset({"racing", "driving"}),
    "sports": frozenset({"sports", "sport"}),
    "survival": frozenset({"survival", "sandbox"}),
    "strategy": frozenset({"strategy", "rts", "tactical"}),
    "mixed": frozenset({"mixed", "variety", "all"}),
}


def _normalize_game(name: str) -> str:
    return name.strip().lower()


def _games_match(a: str, b: str) -> bool:
    na, nb = _normalize_game(a), _normalize_game(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _genre_matches_squad(genre: str, squad: Squad) -> bool:
    g = genre.strip().lower()
    if not g or g == "mixed":
        return bool(squad.genres)
    aliases = GENRE_ALIASES.get(g, frozenset({g}))
    squad_genres = {_normalize_game(x) for x in squad.genres}
    return bool(squad_genres & aliases) or any(
        any(alias in sg or sg in alias for alias in aliases) for sg in squad_genres
    )


@dataclass(frozen=True)
class RecommendationResult:
    scores: dict[str, int]
    best_squad_key: str
    reasons: tuple[str, ...]
    confidence: str
    matched_games: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def score_for(self, key: str) -> int:
        return self.scores.get(key, 0)


def _confidence_from_gap(best: int, second: int) -> str:
    if best <= 0:
        return CONFIDENCE_LOW
    gap = best - second
    if gap >= 4:
        return CONFIDENCE_HIGH
    if gap >= 2:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def recommend(squads: list[Squad], answers: dict[str, Any]) -> RecommendationResult:
    """
    Score each squad from questionnaire answers.

    Expected keys in ``answers``:
    - ``games_played``: list[str]
    - ``most_played``: str
    - ``preferred_genre``: str
    - ``play_frequency``: str (stored for staff; not scored)
    """
    if not squads:
        return RecommendationResult(scores={}, best_squad_key="", reasons=(), confidence=CONFIDENCE_LOW)

    games_played: list[str] = list(answers.get("games_played") or [])
    most_played = str(answers.get("most_played") or "").strip()
    preferred_genre = str(answers.get("preferred_genre") or "").strip()

    scores: dict[str, int] = {s.key: 0 for s in squads}
    matched_games: dict[str, list[str]] = {s.key: [] for s in squads}
    reasons: list[str] = []

    for squad in squads:
        weights: SquadWeights = squad.weights
        for game in games_played:
            for squad_game in squad.games:
                if _games_match(game, squad_game):
                    scores[squad.key] += weights.game_match
                    matched_games[squad.key].append(squad_game)
                    break

        if most_played:
            for squad_game in squad.games:
                if _games_match(most_played, squad_game):
                    scores[squad.key] += weights.most_played_match
                    if squad_game not in matched_games[squad.key]:
                        matched_games[squad.key].append(squad_game)
                    break

        if preferred_genre and _genre_matches_squad(preferred_genre, squad):
            scores[squad.key] += weights.genre_match

    sorted_keys = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)
    best_key = sorted_keys[0]
    second_score = scores[sorted_keys[1]] if len(sorted_keys) > 1 else 0
    confidence = _confidence_from_gap(scores[best_key], second_score)

    best_squad = next(s for s in squads if s.key == best_key)
    best_matches = matched_games.get(best_key, [])

    if best_matches:
        game_list = ", ".join(best_matches)
        reasons.append(
            f"You selected {game_list}, which {'is' if len(best_matches) == 1 else 'are'} "
            f"associated with **{best_squad.name}**."
        )
    elif preferred_genre and preferred_genre.lower() != "mixed":
        reasons.append(
            f"Your preferred genre (**{preferred_genre}**) aligns with **{best_squad.name}**."
        )
    else:
        reasons.append(
            f"**{best_squad.name}** appears to be the best fit based on your gaming profile."
        )

    if confidence == CONFIDENCE_LOW and len(sorted_keys) > 1 and scores[best_key] == scores[sorted_keys[1]]:
        reasons.append("Scores were very close — feel free to choose a different squad if you prefer.")

    return RecommendationResult(
        scores=scores,
        best_squad_key=best_key,
        reasons=tuple(reasons),
        confidence=confidence,
        matched_games={k: tuple(v) for k, v in matched_games.items()},
    )
