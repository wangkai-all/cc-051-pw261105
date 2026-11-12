"""Per-key diagnostics: bucket placement and before/after row accounting.

Every key — hot, ordinary, or empty — is looked up in the *actual* bucket files on
disk, so the numbers shown come from what was written, not from the plan.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from .dataset import sql_string
from .sample_data import EMPTY_KEYS_SQL

EMPTY_SENTINEL = "__empty__"


def key_predicate(key: str) -> str:
    """SQL predicate that selects exactly this key, including both empty forms."""
    if key in (EMPTY_SENTINEL, ""):
        return EMPTY_KEYS_SQL
    return f"join_key = {sql_string(key)}"


def _files_matching(bucket_dir: Path, dataset: str, key: str) -> list[dict]:
    """Read every bucket file for this side and report placement of the key."""
    con = duckdb.connect()
    placements = []
    try:
        for path in sorted(bucket_dir.glob(f"{dataset}-*.parquet")):
            name = path.stem  # e.g. left-salted-03 / left-ordinary-01-02 / left-02
            parts = name.split("-")
            scope = "salted" if "salted" in parts else "ordinary"
            if scope == "salted":
                batch = int(parts[-1]) - 1
                partition = None
            elif len(parts) >= 3:
                batch = int(parts[-2]) - 1
                partition = int(parts[-1]) - 1
            else:
                batch = 0
                partition = int(parts[-1]) - 1
            row_rows = con.execute(
                f"SELECT row_id FROM read_parquet({sql_string(path)}) WHERE {key_predicate(key)} ORDER BY row_id"
            ).fetchall()
            if not row_rows:
                continue
            placements.append(
                {
                    "batch": batch,
                    "scope": scope,
                    "ordinal": partition,
                    "file": path.name,
                    "rows": len(row_rows),
                    "rowIds": [int(row[0]) for row in row_rows],
                }
            )
    finally:
        con.close()
    return placements


def _count_source(path: Path, key: str) -> dict:
    con = duckdb.connect()
    try:
        result = con.execute(
            f"SELECT row_id, payload FROM read_parquet({sql_string(path)}) WHERE {key_predicate(key)} ORDER BY row_id"
        ).fetchall()
    finally:
        con.close()
    return {
        "rows": len(result),
        "rowIds": [int(row[0]) for row in result],
        "payloads": sorted({row[1] for row in result}),
        "payloadCount": len({row[1] for row in result}),
    }


def key_breakdown(*, work_dir: Path, left_path: Path, right_path: Path, key: str) -> dict:
    """Bucket placement, replication and before/after accounting for one key."""
    is_empty = key in (EMPTY_SENTINEL, "")
    bucket_dir = work_dir / "buckets"
    left_source = _count_source(left_path, key)
    right_source = _count_source(right_path, key)

    left_placements = _files_matching(bucket_dir, "left", key)
    right_placements = _files_matching(bucket_dir, "right", key)

    # Join rows from the actual result file.
    con = duckdb.connect()
    try:
        join_rows = int(
            con.execute(
                f"SELECT count(*) FROM read_parquet({sql_string(work_dir / 'result.parquet')}) "
                f"WHERE {key_predicate(key)}"
            ).fetchone()[0]
        )
    finally:
        con.close()

    batches = sorted({item["batch"] for item in left_placements} | {item["batch"] for item in right_placements})
    per_batch = []
    for batch in batches:
        left_rows = sum(item["rows"] for item in left_placements if item["batch"] == batch)
        right_rows = sum(item["rows"] for item in right_placements if item["batch"] == batch)
        per_batch.append(
            {
                "batch": batch,
                "leftRows": left_rows,
                "rightRows": right_rows,
                "joinRows": left_rows * right_rows if not is_empty else 0,
                "scopes": sorted(
                    {item["scope"] for item in left_placements + right_placements if item["batch"] == batch}
                ),
            }
        )

    left_written = sum(item["rows"] for item in left_placements)
    right_written = sum(item["rows"] for item in right_placements)
    row_ids = [row for item in left_placements for row in item["rowIds"]]
    expected_join = 0 if is_empty else left_source["rows"] * right_source["rows"]

    return {
        "key": key,
        "keyLabel": "空键（NULL 与 空字符串）" if is_empty else key,
        "empty": is_empty,
        "mode": "salted" if any(item["scope"] == "salted" for item in left_placements) else "ordinary",
        "batches": per_batch,
        "left": {
            "sourceRows": left_source["rows"],
            "rowsWritten": left_written,
            "lostRows": left_source["rows"] - left_written,
            "duplicateWrites": left_written - len(set(row_ids)),
            "payloadCount": left_source["payloadCount"],
            "buckets": left_placements,
            "rowIds": left_source["rowIds"],
        },
        "right": {
            "sourceRows": right_source["rows"],
            "rowsWritten": right_written,
            "copiesWritten": right_written - right_source["rows"] if not is_empty else 0,
            "payloadCount": right_source["payloadCount"],
            "buckets": right_placements,
            "sampleRowIds": right_source["rowIds"][:8],
        },
        "join": {
            "rows": join_rows,
            "expectedRows": expected_join,
            "matchesLeftTimesRight": join_rows == expected_join,
            "duplicatesPerPair": (
                round(join_rows / expected_join, 3) if expected_join else None
            ),
            "perBatch": per_batch,
        },
        "note": (
            "空键（NULL 与 ''）按 SQL 规则永不匹配；双方各自统计行数并在关联前排除。"
            if is_empty
            else None
        ),
    }
