"""Persisted application settings and the student profile."""

from __future__ import annotations

import sqlite3
from typing import Any

from atlas.config import AppConfig
from atlas.db.core import dumps, loads
from atlas.models.profile import StudentProfile
from atlas.timeutil import now_iso

# Config sections the Settings page may override.
OVERRIDABLE_SECTIONS = ("http", "crawl", "discovery", "llm", "scoring", "verification", "qualification")


def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value_json FROM app_settings WHERE key = ?", (key,)).fetchone()
    return loads(row["value_json"], default) if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO app_settings(key, value_json, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
        (key, dumps(value), now_iso()),
    )


def delete_setting(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))


def effective_config(conn: sqlite3.Connection, base: AppConfig) -> AppConfig:
    """Overlay Settings-page overrides on top of the file/default config."""
    data = base.model_dump(mode="json")
    for section in OVERRIDABLE_SECTIONS:
        override = get_setting(conn, f"config.{section}")
        if isinstance(override, dict):
            data[section] = {**data.get(section, {}), **override}
    cfg = AppConfig.model_validate(data)
    return cfg


def save_config_section(conn: sqlite3.Connection, section: str, values: dict[str, Any]) -> None:
    if section not in OVERRIDABLE_SECTIONS:
        raise ValueError(f"Section {section!r} cannot be overridden from the UI")
    set_setting(conn, f"config.{section}", values)


def load_profile(conn: sqlite3.Connection) -> StudentProfile:
    data = get_setting(conn, "profile")
    if isinstance(data, dict):
        return StudentProfile.model_validate(data)
    return StudentProfile()


def save_profile(conn: sqlite3.Connection, profile: StudentProfile) -> None:
    set_setting(conn, "profile", profile.model_dump(mode="json"))
