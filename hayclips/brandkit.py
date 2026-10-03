"""Local Brand Kit: default caption look and hook settings applied to newly chosen clips."""
from __future__ import annotations

import json

from .look import DEFAULT_HOOK, DEFAULT_LOOK, normalise_hook, normalise_look


def get_brand(conn) -> dict:
    row = conn.execute("SELECT settings, logo_file FROM brand_kit WHERE id = 1").fetchone()
    s = (row or {}).get("settings") or {}
    return {"look": normalise_look(s.get("look")), "hook": normalise_hook(s.get("hook")),
            "logo_file": (row or {}).get("logo_file")}


def save_brand(conn, *, look: dict, hook: dict, logo_file: str | None = ...) -> dict:
    data = {"look": normalise_look(look), "hook": normalise_hook(hook)}
    with conn.transaction():
        if logo_file is ...:
            conn.execute("""INSERT INTO brand_kit (id, settings) VALUES (1, %s)
                            ON CONFLICT (id) DO UPDATE SET settings = EXCLUDED.settings, updated_at = now()""",
                         (json.dumps(data),))
        else:
            conn.execute("""INSERT INTO brand_kit (id, settings, logo_file) VALUES (1, %s, %s)
                            ON CONFLICT (id) DO UPDATE SET settings = EXCLUDED.settings, logo_file = EXCLUDED.logo_file,
                                                           updated_at = now()""", (json.dumps(data), logo_file))
    return get_brand(conn)


def defaults() -> dict:
    return {"look": dict(DEFAULT_LOOK), "hook": dict(DEFAULT_HOOK)}
