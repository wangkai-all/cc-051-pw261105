"""Deterministic sample data.

Both sides carry a stable, unique ``row_id``; business content (payload) repeats on
purpose. Keys can be ``NULL``, ``''`` (both empty, never matching under SQL) or a
normal value. One key is enormous, one is mildly hot and the rest are ordinary so
that an ordinary ``hash(key) % partitions`` layout visibly collapses.

Counts
    HOT-VIP     left 180, right 60   -> 10 800 join rows from a single key
    HOT-API     left  56, right 35   ->  1 960 join rows from a single key
    ordinary    left 1–7, right 1–5  -> a handful of rows each
    empty       left 10 (6 NULL + 4 ''), right 5 (3 NULL + 2 '') -> never match
"""

from __future__ import annotations

HOT_FREQUENCY_THRESHOLD = 20
EMPTY_KEYS_SQL = "(join_key IS NULL OR join_key = '')"
NONEMPTY_SQL = "(join_key IS NOT NULL AND join_key <> '')"
MAX_HOT_CHOICES = 6
MAX_PLAN_PARTITIONS = 64
MIN_SALT = 2
MAX_SALT = 32
LEFT_COLUMNS = (("row_id", "BIGINT"), ("join_key", "VARCHAR"), ("payload", "VARCHAR"))
RIGHT_COLUMNS = LEFT_COLUMNS

SAMPLE_LEFT_KEYS = {
    "HOT-VIP": 180,
    "HOT-API": 56,
    "CUST-1001": 3,
    "CUST-1002": 7,
    "CUST-3001": 2,
    "CUST-3002": 1,
    "CUST-3003": 1,
}
SAMPLE_RIGHT_KEYS = {
    "HOT-VIP": 60,
    "HOT-API": 35,
    "CUST-1001": 5,
    "CUST-1002": 4,
    "CUST-3001": 1,
    "CUST-3002": 2,
}
# (side, "NULL" | "EMPTY", row count)
SAMPLE_EMPTY_KEYS = (("left", "NULL", 6), ("left", "EMPTY", 4), ("right", "NULL", 3), ("right", "EMPTY", 2))


def _rows(side: str) -> list[tuple[int, str | None, str]]:
    keys = SAMPLE_LEFT_KEYS if side == "left" else SAMPLE_RIGHT_KEYS
    modulo = 3 if side == "left" else 4
    rows: list[tuple[int, str | None, str]] = []
    for key, count in keys.items():
        for index in range(count):
            rows.append((len(rows) + 1, key, f"{key}-payload-{index % modulo}"))
    for empty_side, kind, count in SAMPLE_EMPTY_KEYS:
        if empty_side != side:
            continue
        value = None if kind == "NULL" else ""
        for index in range(count):
            rows.append((len(rows) + 1, value, f"EMPTY-{kind}-{index % 2}"))
    return rows


SAMPLE_LEFT_ROWS = _rows("left")
SAMPLE_RIGHT_ROWS = _rows("right")
