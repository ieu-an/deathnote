"""
SQLite persistence layer for per-guild settings and recruitment applications.

Uses aiosqlite for async-safe access from discord.py coroutines.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from utils.squads import Squad, row_to_squad

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "bot.db"

# Application lifecycle status values stored in DB
STATUS_SUBMITTED = "submitted"
STATUS_INTERVIEW = "interview"
STATUS_ACCEPTED = "accepted"
STATUS_DENIED = "denied"

ACTIVE_STATUSES = frozenset({STATUS_SUBMITTED, STATUS_INTERVIEW})


@dataclass(frozen=True)
class GuildSettings:
    """Snapshot of a row in ``guild_settings``."""

    guild_id: int
    welcome_enabled: bool
    welcome_message: str
    welcome_channel_id: Optional[int]
    ping_enabled: bool
    ping_start: str  # HH:MM (interpreted in America/New_York)
    ping_end: str
    ping_role_id: Optional[int]
    night_ping_role_id: Optional[int]  # mentioned outside [ping_start, ping_end] when ping_enabled
    verification_channel_id: Optional[int]
    staff_channel_id: Optional[int]
    unverified_role_id: Optional[int]
    botmod_role_id: Optional[int]
    accepted_role_id: Optional[int]
    interview_role_id: Optional[int]
    staff_role_id: Optional[int]
    log_channel_id: Optional[int]
    interview_category_id: Optional[int]
    staff_role_panel_channel_id: Optional[int]
    staff_role_panel_message_id: Optional[int]


@dataclass(frozen=True)
class PurgeUserDataResult:
    """Counts from :meth:`Database.purge_user_data`."""

    applications_deleted: int
    pending_deleted: int
    recruiter_claims_cleared: int


@dataclass(frozen=True)
class Application:
    """Row in ``applications``."""

    id: int
    guild_id: int
    user_id: int
    status: str
    division: str
    recruiter_id: Optional[int]
    submitted_at: int  # UTC unix seconds
    interview_started_at: Optional[int]
    staff_message_id: Optional[int]
    staff_channel_id: Optional[int]
    thread_id: Optional[int]
    answers_json: str
    selected_squad: str
    recommended_squad: str
    selection_method: str
    questionnaire_json: str
    squad_scores_json: str


def _row_to_settings(row: aiosqlite.Row) -> GuildSettings:
    return GuildSettings(
        guild_id=row["guild_id"],
        welcome_enabled=bool(row["welcome_enabled"]),
        welcome_message=row["welcome_message"] or "",
        welcome_channel_id=row["welcome_channel_id"],
        ping_enabled=bool(row["ping_enabled"]),
        ping_start=row["ping_start"] or "09:00",
        ping_end=row["ping_end"] or "21:00",
        ping_role_id=row["ping_role_id"],
        night_ping_role_id=row["night_ping_role_id"],
        verification_channel_id=row["verification_channel_id"],
        staff_channel_id=row["staff_channel_id"],
        unverified_role_id=row["unverified_role_id"],
        botmod_role_id=row["botmod_role_id"],
        accepted_role_id=row["accepted_role_id"],
        interview_role_id=row["interview_role_id"],
        staff_role_id=row["staff_role_id"],
        log_channel_id=row["log_channel_id"],
        interview_category_id=row["interview_category_id"],
        staff_role_panel_channel_id=row["staff_role_panel_channel_id"],
        staff_role_panel_message_id=row["staff_role_panel_message_id"],
    )


def _row_to_application(row: aiosqlite.Row) -> Application:
    return Application(
        id=row["id"],
        guild_id=row["guild_id"],
        user_id=row["user_id"],
        status=row["status"],
        division=row["division"] or "",
        recruiter_id=row["recruiter_id"],
        submitted_at=int(row["submitted_at"]),
        interview_started_at=int(row["interview_started_at"]) if row["interview_started_at"] is not None else None,
        staff_message_id=row["staff_message_id"],
        staff_channel_id=row["staff_channel_id"],
        thread_id=row["thread_id"],
        answers_json=row["answers_json"] or "{}",
        selected_squad=str(row["selected_squad"] or "") if "selected_squad" in row.keys() else "",
        recommended_squad=str(row["recommended_squad"] or "") if "recommended_squad" in row.keys() else "",
        selection_method=str(row["selection_method"] or "") if "selection_method" in row.keys() else "",
        questionnaire_json=str(row["questionnaire_json"] or "{}") if "questionnaire_json" in row.keys() else "{}",
        squad_scores_json=str(row["squad_scores_json"] or "{}") if "squad_scores_json" in row.keys() else "{}",
    )


class Database:
    """Thin async wrapper around SQLite for guild configuration and applications."""

    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self._path = Path(path)

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._migrate()
        await self._conn.commit()
        logger.info("Database ready at %s", self._path)

    async def close(self) -> None:
        if hasattr(self, "_conn") and self._conn:
            await self._conn.close()

    async def _column_exists(self, table: str, column: str) -> bool:
        async with self._conn.execute(f"PRAGMA table_info({table})") as cursor:
            rows = await cursor.fetchall()
        return any(r[1] == column for r in rows)

    async def _add_column_if_missing(self, table: str, column: str, definition: str) -> None:
        if not await self._column_exists(table, column):
            await self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    async def _migrate(self) -> None:
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY NOT NULL,
                welcome_enabled INTEGER NOT NULL DEFAULT 0,
                welcome_message TEXT NOT NULL DEFAULT '',
                welcome_channel_id INTEGER,
                ping_enabled INTEGER NOT NULL DEFAULT 0,
                ping_start TEXT NOT NULL DEFAULT '09:00',
                ping_end TEXT NOT NULL DEFAULT '21:00',
                ping_role_id INTEGER,
                verification_channel_id INTEGER,
                staff_channel_id INTEGER,
                unverified_role_id INTEGER,
                botmod_role_id INTEGER
            )
            """
        )

        await self._add_column_if_missing("guild_settings", "welcome_channel_id", "INTEGER")
        await self._add_column_if_missing("guild_settings", "accepted_role_id", "INTEGER")
        await self._add_column_if_missing("guild_settings", "interview_role_id", "INTEGER")
        await self._add_column_if_missing("guild_settings", "staff_role_id", "INTEGER")
        await self._add_column_if_missing("guild_settings", "log_channel_id", "INTEGER")
        await self._add_column_if_missing("guild_settings", "interview_category_id", "INTEGER")
        await self._add_column_if_missing("guild_settings", "night_ping_role_id", "INTEGER")
        await self._add_column_if_missing("guild_settings", "staff_role_panel_channel_id", "INTEGER")
        await self._add_column_if_missing("guild_settings", "staff_role_panel_message_id", "INTEGER")

        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                division TEXT NOT NULL DEFAULT '',
                recruiter_id INTEGER,
                submitted_at INTEGER NOT NULL,
                interview_started_at INTEGER,
                staff_message_id INTEGER,
                staff_channel_id INTEGER,
                thread_id INTEGER,
                answers_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )

        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_applications_guild_user ON applications(guild_id, user_id)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_applications_staff_msg ON applications(staff_message_id)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_applications_status_interview ON applications(status, interview_started_at)"
        )

        await self._add_column_if_missing("applications", "selected_squad", "TEXT NOT NULL DEFAULT ''")
        await self._add_column_if_missing("applications", "recommended_squad", "TEXT NOT NULL DEFAULT ''")
        await self._add_column_if_missing("applications", "selection_method", "TEXT NOT NULL DEFAULT ''")
        await self._add_column_if_missing("applications", "questionnaire_json", "TEXT NOT NULL DEFAULT '{}'")
        await self._add_column_if_missing("applications", "squad_scores_json", "TEXT NOT NULL DEFAULT '{}'")

        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS squads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                emoji TEXT NOT NULL DEFAULT '',
                role_id INTEGER,
                leader_role_id INTEGER,
                channel_id INTEGER,
                recruiter_role_id INTEGER,
                games_json TEXT NOT NULL DEFAULT '[]',
                genres_json TEXT NOT NULL DEFAULT '[]',
                weights_json TEXT NOT NULL DEFAULT '{}',
                welcome_message TEXT NOT NULL DEFAULT '',
                UNIQUE(guild_id, key)
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_squads_guild ON squads(guild_id)"
        )

        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS division_routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                match_key TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_division_routes_guild ON division_routes(guild_id)"
        )

        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                answers_json TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )

        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reaction_role_bindings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                emoji_key TEXT NOT NULL,
                role_id INTEGER NOT NULL,
                UNIQUE(guild_id, message_id, emoji_key)
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reaction_role_msg ON reaction_role_bindings(guild_id, message_id)"
        )

    async def ensure_guild(self, guild_id: int) -> None:
        await self._conn.execute(
            """
            INSERT INTO guild_settings (guild_id)
            VALUES (?)
            ON CONFLICT(guild_id) DO NOTHING
            """,
            (guild_id,),
        )
        await self._conn.commit()

    async def get_guild_settings(self, guild_id: int) -> GuildSettings:
        await self.ensure_guild(guild_id)
        async with self._conn.execute(
            "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        return _row_to_settings(row)

    async def update_guild_settings(self, guild_id: int, **fields: Any) -> GuildSettings:
        """
        Update only provided columns. Column names must match the table schema.
        Booleans are stored as 0/1.
        """
        await self.ensure_guild(guild_id)
        if not fields:
            return await self.get_guild_settings(guild_id)

        columns: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in _ALLOWED_GUILD_COLUMNS:
                raise ValueError(f"Unknown guild_settings column: {key}")
            columns.append(f"{key} = ?")
            if isinstance(value, bool):
                values.append(1 if value else 0)
            else:
                values.append(value)

        values.append(guild_id)
        query = f"UPDATE guild_settings SET {', '.join(columns)} WHERE guild_id = ?"
        await self._conn.execute(query, values)
        await self._conn.commit()
        logger.info("Updated guild_settings for %s: %s", guild_id, list(fields.keys()))
        return await self.get_guild_settings(guild_id)

    # --- Applications ---

    async def purge_user_data(self, guild_id: int, user_id: int) -> PurgeUserDataResult:
        """
        Remove stored recruitment data for ``user_id`` in ``guild_id``.

        Deletes ``applications`` and ``pending_applications`` rows where this user is the applicant,
        and clears ``recruiter_id`` on other users' applications when it referenced this user.
        """
        cur1 = await self._conn.execute(
            "DELETE FROM pending_applications WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        pending_deleted = cur1.rowcount if cur1.rowcount is not None else 0

        cur2 = await self._conn.execute(
            "DELETE FROM applications WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        applications_deleted = cur2.rowcount if cur2.rowcount is not None else 0

        cur3 = await self._conn.execute(
            "UPDATE applications SET recruiter_id = NULL WHERE guild_id = ? AND recruiter_id = ?",
            (guild_id, user_id),
        )
        recruiter_claims_cleared = cur3.rowcount if cur3.rowcount is not None else 0

        await self._conn.commit()
        logger.info(
            "Purged user data guild=%s user=%s apps=%s pending=%s recruiter_refs=%s",
            guild_id,
            user_id,
            applications_deleted,
            pending_deleted,
            recruiter_claims_cleared,
        )
        return PurgeUserDataResult(
            applications_deleted=applications_deleted,
            pending_deleted=pending_deleted,
            recruiter_claims_cleared=recruiter_claims_cleared,
        )

    async def user_has_active_application(self, guild_id: int, user_id: int) -> bool:
        async with self._conn.execute(
            """
            SELECT 1 FROM applications
            WHERE guild_id = ? AND user_id = ? AND status IN ('submitted', 'interview')
            LIMIT 1
            """,
            (guild_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        return row is not None

    async def insert_application(
        self,
        guild_id: int,
        user_id: int,
        *,
        status: str,
        division: str,
        answers: dict[str, Any],
        submitted_at: Optional[int] = None,
        selected_squad: str = "",
        recommended_squad: str = "",
        selection_method: str = "",
        questionnaire: Optional[dict[str, Any]] = None,
        squad_scores: Optional[dict[str, Any]] = None,
    ) -> int:
        ts = submitted_at if submitted_at is not None else int(time.time())
        payload = json.dumps(answers, ensure_ascii=False)
        questionnaire_payload = json.dumps(questionnaire or {}, ensure_ascii=False)
        scores_payload = json.dumps(squad_scores or {}, ensure_ascii=False)
        cur = await self._conn.execute(
            """
            INSERT INTO applications (
                guild_id, user_id, status, division, submitted_at, answers_json,
                selected_squad, recommended_squad, selection_method,
                questionnaire_json, squad_scores_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                user_id,
                status,
                division,
                ts,
                payload,
                selected_squad,
                recommended_squad,
                selection_method,
                questionnaire_payload,
                scores_payload,
            ),
        )
        await self._conn.commit()
        app_id = cur.lastrowid
        assert app_id is not None
        logger.info("Inserted application id=%s guild=%s user=%s", app_id, guild_id, user_id)
        return int(app_id)

    async def update_application_staff_message(
        self,
        application_id: int,
        *,
        staff_message_id: int,
        staff_channel_id: int,
    ) -> None:
        await self._conn.execute(
            """
            UPDATE applications
            SET staff_message_id = ?, staff_channel_id = ?
            WHERE id = ?
            """,
            (staff_message_id, staff_channel_id, application_id),
        )
        await self._conn.commit()

    async def get_application_by_staff_message(self, guild_id: int, staff_message_id: int) -> Optional[Application]:
        async with self._conn.execute(
            """
            SELECT * FROM applications
            WHERE guild_id = ? AND staff_message_id = ?
            """,
            (guild_id, staff_message_id),
        ) as cursor:
            row = await cursor.fetchone()
        return _row_to_application(row) if row else None

    async def get_application_by_id(self, application_id: int) -> Optional[Application]:
        async with self._conn.execute("SELECT * FROM applications WHERE id = ?", (application_id,)) as cursor:
            row = await cursor.fetchone()
        return _row_to_application(row) if row else None

    async def delete_application_by_id(self, application_id: int) -> None:
        await self._conn.execute("DELETE FROM applications WHERE id = ?", (application_id,))
        await self._conn.commit()

    async def get_application_by_thread_id(self, guild_id: int, thread_id: int) -> Optional[Application]:
        async with self._conn.execute(
            """
            SELECT * FROM applications
            WHERE guild_id = ? AND thread_id = ?
            """,
            (guild_id, thread_id),
        ) as cursor:
            row = await cursor.fetchone()
        return _row_to_application(row) if row else None

    async def update_application_status(
        self,
        application_id: int,
        *,
        status: Optional[str] = None,
        interview_started_at: Optional[int] = None,
        recruiter_id: Optional[int] = None,
        thread_id: Optional[int] = None,
    ) -> None:
        fields: list[str] = []
        values: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if interview_started_at is not None:
            fields.append("interview_started_at = ?")
            values.append(interview_started_at)
        if recruiter_id is not None:
            fields.append("recruiter_id = ?")
            values.append(recruiter_id)
        if thread_id is not None:
            fields.append("thread_id = ?")
            values.append(thread_id)
        if not fields:
            return
        values.append(application_id)
        await self._conn.execute(
            f"UPDATE applications SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        await self._conn.commit()

    async def set_application_recruiter(self, application_id: int, recruiter_id: int) -> None:
        await self._conn.execute(
            "UPDATE applications SET recruiter_id = ? WHERE id = ?",
            (recruiter_id, application_id),
        )
        await self._conn.commit()

    async def list_interview_timeouts(self, before_unix: int) -> list[Application]:
        """Applications in interview state whose interview_started_at is before ``before_unix``."""
        async with self._conn.execute(
            """
            SELECT * FROM applications
            WHERE status = 'interview'
              AND interview_started_at IS NOT NULL
              AND interview_started_at < ?
            """,
            (before_unix,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_application(r) for r in rows]

    # --- Pending (legacy; kept for DB compatibility / manual use) ---

    async def insert_pending_application(self, guild_id: int, user_id: int, answers: dict[str, Any]) -> int:
        await self._purge_stale_pending()
        await self._conn.execute(
            "DELETE FROM pending_applications WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        payload = json.dumps(answers, ensure_ascii=False)
        ts = int(time.time())
        cur = await self._conn.execute(
            """
            INSERT INTO pending_applications (guild_id, user_id, answers_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (guild_id, user_id, payload, ts),
        )
        await self._conn.commit()
        pid = cur.lastrowid
        assert pid is not None
        return int(pid)

    async def get_pending_application(self, pending_id: int) -> Optional[tuple[int, int, dict[str, Any]]]:
        async with self._conn.execute(
            "SELECT guild_id, user_id, answers_json FROM pending_applications WHERE id = ?",
            (pending_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        answers = json.loads(row["answers_json"] or "{}")
        return int(row["guild_id"]), int(row["user_id"]), answers

    async def delete_pending_application(self, pending_id: int) -> None:
        await self._conn.execute("DELETE FROM pending_applications WHERE id = ?", (pending_id,))
        await self._conn.commit()

    async def _purge_stale_pending(self) -> None:
        cutoff = int(time.time()) - 3600
        await self._conn.execute("DELETE FROM pending_applications WHERE created_at < ?", (cutoff,))

    # --- Squads ---

    async def list_squads(self, guild_id: int) -> list[Squad]:
        async with self._conn.execute(
            "SELECT * FROM squads WHERE guild_id = ? ORDER BY name ASC",
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [row_to_squad(r) for r in rows]

    async def get_squad_by_key(self, guild_id: int, key: str) -> Optional[Squad]:
        async with self._conn.execute(
            "SELECT * FROM squads WHERE guild_id = ? AND key = ?",
            (guild_id, key.strip().lower()),
        ) as cursor:
            row = await cursor.fetchone()
        return row_to_squad(row) if row else None

    async def get_squad_by_id(self, squad_id: int) -> Optional[Squad]:
        async with self._conn.execute("SELECT * FROM squads WHERE id = ?", (squad_id,)) as cursor:
            row = await cursor.fetchone()
        return row_to_squad(row) if row else None

    async def insert_squad(
        self,
        guild_id: int,
        *,
        key: str,
        name: str,
        description: str = "",
        emoji: str = "",
        role_id: Optional[int] = None,
        leader_role_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        recruiter_role_id: Optional[int] = None,
        games: Optional[list[str]] = None,
        genres: Optional[list[str]] = None,
        weights: Optional[dict[str, Any]] = None,
        welcome_message: str = "",
    ) -> Squad:
        games_payload = json.dumps(games or [], ensure_ascii=False)
        genres_payload = json.dumps(genres or [], ensure_ascii=False)
        weights_payload = json.dumps(weights or {}, ensure_ascii=False)
        cur = await self._conn.execute(
            """
            INSERT INTO squads (
                guild_id, key, name, description, emoji,
                role_id, leader_role_id, channel_id, recruiter_role_id,
                games_json, genres_json, weights_json, welcome_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                key.strip().lower(),
                name,
                description,
                emoji,
                role_id,
                leader_role_id,
                channel_id,
                recruiter_role_id,
                games_payload,
                genres_payload,
                weights_payload,
                welcome_message,
            ),
        )
        await self._conn.commit()
        squad_id = cur.lastrowid
        assert squad_id is not None
        squad = await self.get_squad_by_id(int(squad_id))
        assert squad is not None
        logger.info("Inserted squad key=%s guild=%s", key, guild_id)
        return squad

    async def update_squad(self, squad_id: int, **fields: Any) -> Optional[Squad]:
        if not fields:
            return await self.get_squad_by_id(squad_id)

        columns: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in _ALLOWED_SQUAD_COLUMNS:
                raise ValueError(f"Unknown squads column: {key}")
            if key in ("games_json", "genres_json", "weights_json") and not isinstance(value, str):
                columns.append(f"{key} = ?")
                values.append(json.dumps(value, ensure_ascii=False))
            else:
                columns.append(f"{key} = ?")
                values.append(value)

        values.append(squad_id)
        await self._conn.execute(
            f"UPDATE squads SET {', '.join(columns)} WHERE id = ?",
            values,
        )
        await self._conn.commit()
        logger.info("Updated squad id=%s fields=%s", squad_id, list(fields.keys()))
        return await self.get_squad_by_id(squad_id)

    async def delete_squad(self, guild_id: int, key: str) -> bool:
        cur = await self._conn.execute(
            "DELETE FROM squads WHERE guild_id = ? AND key = ?",
            (guild_id, key.strip().lower()),
        )
        await self._conn.commit()
        return (cur.rowcount or 0) > 0

    # --- Division routes ---

    async def list_division_routes(self, guild_id: int) -> list[tuple[int, str, int]]:
        """Rows: (id, match_key, channel_id) ordered for matching."""
        async with self._conn.execute(
            """
            SELECT id, match_key, channel_id FROM division_routes
            WHERE guild_id = ?
            ORDER BY priority DESC, id ASC
            """,
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [(int(r["id"]), str(r["match_key"]), int(r["channel_id"])) for r in rows]

    async def resolve_division_channel(self, guild_id: int, division_label: str) -> Optional[int]:
        """
        Return channel_id for first route whose match_key appears in the normalized label,
        or None to use default staff channel.
        """
        norm = (division_label or "").strip().lower()
        if not norm:
            return None
        routes = await self.list_division_routes(guild_id)
        for _rid, key, channel_id in routes:
            k = key.strip().lower()
            if k and (k in norm or norm in k or k == norm):
                return channel_id
        return None

    # --- Reaction roles ---

    async def add_reaction_role_bindings(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        pairs: list[tuple[str, int]],
    ) -> None:
        """Insert emoji_key → role_id rows for a reaction-role panel message."""
        for emoji_key, role_id in pairs:
            await self._conn.execute(
                """
                INSERT INTO reaction_role_bindings (guild_id, channel_id, message_id, emoji_key, role_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, channel_id, message_id, emoji_key, role_id),
            )
        await self._conn.commit()

    async def get_reaction_role_for_emoji(
        self,
        guild_id: int,
        message_id: int,
        emoji_key: str,
    ) -> Optional[int]:
        async with self._conn.execute(
            """
            SELECT role_id FROM reaction_role_bindings
            WHERE guild_id = ? AND message_id = ? AND emoji_key = ?
            LIMIT 1
            """,
            (guild_id, message_id, emoji_key),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["role_id"]) if row else None


_ALLOWED_GUILD_COLUMNS = frozenset(
    {
        "welcome_enabled",
        "welcome_message",
        "welcome_channel_id",
        "ping_enabled",
        "ping_start",
        "ping_end",
        "ping_role_id",
        "night_ping_role_id",
        "verification_channel_id",
        "staff_channel_id",
        "unverified_role_id",
        "botmod_role_id",
        "accepted_role_id",
        "interview_role_id",
        "staff_role_id",
        "log_channel_id",
        "interview_category_id",
        "staff_role_panel_channel_id",
        "staff_role_panel_message_id",
    }
)

_ALLOWED_SQUAD_COLUMNS = frozenset(
    {
        "key",
        "name",
        "description",
        "emoji",
        "role_id",
        "leader_role_id",
        "channel_id",
        "recruiter_role_id",
        "games_json",
        "genres_json",
        "weights_json",
        "welcome_message",
    }
)
