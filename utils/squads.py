"""
Squad configuration model and helpers for multi-squad recruitment.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import aiosqlite

SELECTION_MANUAL = "manual"
SELECTION_RECOMMENDED = "recommended"


def normalize_squad_key(raw: str) -> str:
    """Lowercase slug suitable for DB storage and custom_id suffixes."""
    key = re.sub(r"[^a-z0-9]+", "-", raw.strip().lower())
    key = re.sub(r"-+", "-", key).strip("-")
    return key[:64]


def parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(x).strip() for x in data if str(x).strip()]


def parse_json_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class SquadWeights:
    """Default recommendation scoring weights; overridable per squad."""

    game_match: int = 3
    most_played_match: int = 5
    genre_match: int = 2

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SquadWeights:
        return cls(
            game_match=int(data.get("game_match", 3)),
            most_played_match=int(data.get("most_played_match", 5)),
            genre_match=int(data.get("genre_match", 2)),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "game_match": self.game_match,
            "most_played_match": self.most_played_match,
            "genre_match": self.genre_match,
        }


@dataclass(frozen=True)
class Squad:
    """Configured recruitment squad for a guild."""

    id: int
    guild_id: int
    key: str
    name: str
    description: str
    emoji: str
    role_id: Optional[int]
    leader_role_id: Optional[int]
    channel_id: Optional[int]
    recruiter_role_id: Optional[int]
    games: tuple[str, ...] = field(default_factory=tuple)
    genres: tuple[str, ...] = field(default_factory=tuple)
    weights: SquadWeights = field(default_factory=SquadWeights)
    welcome_message: str = ""

    @property
    def display_name(self) -> str:
        if self.emoji:
            return f"{self.emoji} {self.name}"
        return self.name

    def games_display(self) -> str:
        if not self.games:
            return "—"
        return "\n".join(f"• {g}" for g in self.games)


def row_to_squad(row: aiosqlite.Row) -> Squad:
    weights_data = parse_json_dict(row["weights_json"])
    return Squad(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        key=str(row["key"]),
        name=str(row["name"]),
        description=str(row["description"] or ""),
        emoji=str(row["emoji"] or ""),
        role_id=row["role_id"],
        leader_role_id=row["leader_role_id"],
        channel_id=row["channel_id"],
        recruiter_role_id=row["recruiter_role_id"],
        games=tuple(parse_json_list(row["games_json"])),
        genres=tuple(parse_json_list(row["genres_json"])),
        weights=SquadWeights.from_dict(weights_data),
        welcome_message=str(row["welcome_message"] or ""),
    )


def squad_by_key(squads: list[Squad], key: str) -> Optional[Squad]:
    norm = key.strip().lower()
    for squad in squads:
        if squad.key == norm:
            return squad
    return None
