"""Postgres access (psycopg 3). Connection settings come from HAYCLIPS_DATABASE_URL.

Default: the local socket-only development cluster made by scripts/dev-postgres.sh.
Migrations are plain SQL files in hayclips/migrations, applied in name order, once.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

MIGRATIONS = Path(__file__).parent / "migrations"


def default_dsn() -> str:
    home = Path(os.environ.get("HAYCLIPS_HOME") or Path.home() / ".hayclips")
    return f"host={home / 'pgsock'} port={os.environ.get('HAYCLIPS_PG_PORT', '54329')} user=hayclips dbname=hayclips"


def dsn() -> str:
    return os.environ.get("HAYCLIPS_DATABASE_URL") or default_dsn()


def connect(conninfo: str | None = None, autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(conninfo or dsn(), row_factory=dict_row, autocommit=autocommit)


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply pending migrations; returns the names applied."""
    applied = []
    with conn.transaction():
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        conn.execute("SELECT pg_advisory_xact_lock(4242001)")
        done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.name in done:
                continue
            conn.execute(f.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
            applied.append(f.name)
    return applied
