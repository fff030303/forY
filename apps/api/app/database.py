import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "persona_companion.db"

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS projects (
 id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, display_name TEXT NOT NULL,
 relationship_type TEXT NOT NULL, consent_status TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft', target_speaker TEXT, user_speaker TEXT,
 active_version_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_messages (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 fingerprint TEXT NOT NULL, speaker TEXT NOT NULL, sent_at TEXT,
 normalized_text TEXT NOT NULL, source_line INTEGER NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(project_id, fingerprint)
);
CREATE TABLE IF NOT EXISTS import_jobs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 source_type TEXT NOT NULL, source_chat TEXT NOT NULL, self_speaker TEXT NOT NULL,
 since_date TEXT, until_date TEXT, status TEXT NOT NULL,
 page_size INTEGER NOT NULL, next_offset INTEGER NOT NULL DEFAULT 0,
 imported_count INTEGER NOT NULL DEFAULT 0,
 duplicate_count INTEGER NOT NULL DEFAULT 0,
 chunk_count INTEGER NOT NULL DEFAULT 0,
 analyzed_chunk_count INTEGER NOT NULL DEFAULT 0,
 import_complete INTEGER NOT NULL DEFAULT 0,
 analyze_requested INTEGER NOT NULL DEFAULT 1,
 summary_json TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_messages (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 import_job_id TEXT NOT NULL REFERENCES import_jobs(id) ON DELETE CASCADE,
 source_message_id TEXT, source_chat TEXT NOT NULL, speaker TEXT NOT NULL,
 sent_at TEXT, source_timestamp INTEGER, raw_text TEXT NOT NULL,
 normalized_text TEXT NOT NULL, fingerprint TEXT NOT NULL,
 source_offset INTEGER NOT NULL, is_analyzed INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, UNIQUE(project_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_raw_messages_job
 ON raw_messages(import_job_id, source_timestamp, source_offset);
CREATE TABLE IF NOT EXISTS analysis_chunks (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 import_job_id TEXT NOT NULL REFERENCES import_jobs(id) ON DELETE CASCADE,
 chunk_index INTEGER NOT NULL, status TEXT NOT NULL,
 message_ids_json TEXT NOT NULL, message_count INTEGER NOT NULL,
 token_estimate INTEGER NOT NULL, started_at TEXT, ended_at TEXT,
 analysis_json TEXT, prompt_tokens INTEGER NOT NULL DEFAULT 0,
 completion_tokens INTEGER NOT NULL DEFAULT 0,
 cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
 cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
 error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(import_job_id, chunk_index)
);
CREATE TABLE IF NOT EXISTS persona_versions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 version_number INTEGER NOT NULL, status TEXT NOT NULL, summary TEXT NOT NULL,
 traits_json TEXT NOT NULL, relationship_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(project_id, version_number)
);
CREATE TABLE IF NOT EXISTS memories (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 version_id TEXT REFERENCES persona_versions(id) ON DELETE SET NULL,
 content TEXT NOT NULL, importance REAL NOT NULL, event_date TEXT,
 source_message_ids_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dialogue_examples (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 version_id TEXT REFERENCES persona_versions(id) ON DELETE SET NULL,
 context_text TEXT NOT NULL, reply_text TEXT NOT NULL,
 source_message_ids_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
 id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 role TEXT NOT NULL, content TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 message_id TEXT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
 rating TEXT NOT NULL, reason TEXT, ideal_reply TEXT,
 status TEXT NOT NULL DEFAULT 'candidate', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS life_states (
 project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
 timezone TEXT NOT NULL, activity TEXT NOT NULL, location TEXT NOT NULL,
 mood TEXT NOT NULL, condition TEXT NOT NULL,
 energy INTEGER NOT NULL, hunger INTEGER NOT NULL, sleepiness INTEGER NOT NULL,
 health INTEGER NOT NULL, stress INTEGER NOT NULL,
 last_simulated_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS life_events (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 event_key TEXT NOT NULL, event_type TEXT NOT NULL, title TEXT NOT NULL,
 description TEXT NOT NULL, location TEXT NOT NULL, started_at TEXT NOT NULL,
 source TEXT NOT NULL DEFAULT 'evidence_inference',
 confidence REAL NOT NULL DEFAULT 0,
 evidence_message_ids_json TEXT NOT NULL DEFAULT '[]',
 basis TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
 UNIQUE(project_id, event_key)
);
CREATE INDEX IF NOT EXISTS idx_life_events_project_time
 ON life_events(project_id, started_at);
CREATE TABLE IF NOT EXISTS life_settings (
 project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
 guidance TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS life_daily_plans (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 plan_date TEXT NOT NULL, timezone TEXT NOT NULL, guidance TEXT NOT NULL,
 plan_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(project_id, plan_date)
);
"""


def database_path() -> Path:
    return Path(os.getenv("PERSONA_DB_PATH", str(DEFAULT_DB_PATH)))


def init_database() -> None:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        memory_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(memories)")
        }
        if "event_date" not in memory_columns:
            connection.execute("ALTER TABLE memories ADD COLUMN event_date TEXT")
        life_event_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(life_events)")
        }
        if "confidence" not in life_event_columns:
            connection.execute(
                "ALTER TABLE life_events ADD COLUMN confidence REAL NOT NULL DEFAULT 0"
            )
        if "evidence_message_ids_json" not in life_event_columns:
            connection.execute(
                """ALTER TABLE life_events ADD COLUMN
                evidence_message_ids_json TEXT NOT NULL DEFAULT '[]'"""
            )
        if "basis" not in life_event_columns:
            connection.execute(
                "ALTER TABLE life_events ADD COLUMN basis TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            """DELETE FROM life_events
            WHERE source = 'simulated'
            OR (source = 'evidence_inference'
                AND evidence_message_ids_json = '[]')"""
        )
        memories_without_dates = connection.execute(
            """SELECT id, source_message_ids_json FROM memories
            WHERE event_date IS NULL"""
        ).fetchall()
        for memory_id, source_ids_json in memories_without_dates:
            try:
                source_ids = json.loads(source_ids_json or "[]")
            except json.JSONDecodeError:
                continue
            source_ids = [
                source_id for source_id in source_ids
                if isinstance(source_id, str) and source_id
            ]
            if not source_ids:
                continue
            placeholders = ",".join("?" for _ in source_ids)
            row = connection.execute(
                f"""SELECT MIN(SUBSTR(sent_at, 1, 10)) FROM source_messages
                WHERE id IN ({placeholders}) AND sent_at IS NOT NULL""",
                source_ids,
            ).fetchone()
            if row and row[0]:
                connection.execute(
                    "UPDATE memories SET event_date = ? WHERE id = ?",
                    (row[0], memory_id),
                )


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database_path())
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    result = dict(row)
    for key in (
        "traits_json",
        "relationship_json",
        "source_message_ids_json",
        "evidence_message_ids_json",
        "message_ids_json",
        "analysis_json",
        "summary_json",
        "plan_json",
    ):
        if key in result:
            value = result.pop(key)
            result[key.removesuffix("_json")] = json.loads(value) if value else None
    return result
