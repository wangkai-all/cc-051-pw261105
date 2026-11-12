"""Dataset import, validation and profiling.

Accepted schema (both files):

    row_id    integer   stable and unique within its side — never repeated
    join_key  string    NULL and '' are "empty": they follow SQL rules and match nothing
    payload   string    optional business content; may repeat freely

The built-in sample is always re-materialised as two Parquet files on disk, so
``row_id`` stays stable for every later read. Uploads are normalised to the same
canonical three-column schema before use.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .sample_data import (
    EMPTY_KEYS_SQL,
    HOT_FREQUENCY_THRESHOLD,
    NONEMPTY_SQL,
    SAMPLE_LEFT_ROWS,
    SAMPLE_RIGHT_ROWS,
)

DATA_ROOT = Path(os.environ.get("LAB_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
MAX_DATASET_ROWS = int(os.environ.get("LAB_MAX_ROWS", "2000000"))
KEY_PAGE_SIZE = 200
CANONICAL_SCHEMA = pa.schema(
    [pa.field("row_id", pa.int64()), pa.field("join_key", pa.string()), pa.field("payload", pa.string())]
)


class DatasetError(ValueError):
    """The supplied dataset cannot be used."""


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def dataset_root(dataset_id: str) -> Path:
    path = DATA_ROOT / "datasets" / dataset_id
    if not path.is_dir():
        raise DatasetError(f"unknown dataset {dataset_id!r}")
    return path


# --------------------------------------------------------------------------- #
# Validation & profiling
# --------------------------------------------------------------------------- #


def _table_rows(path: Path) -> int:
    return pq.ParquetFile(path).metadata.num_rows


def _read_side(con: duckdb.DuckDBPyConnection, path: Path, side: str) -> dict:
    source = f"read_parquet({sql_string(path)})"
    total = _table_rows(path)
    if total > MAX_DATASET_ROWS:
        raise DatasetError(
            f"{side}: {total:,} rows exceeds the lab limit of {MAX_DATASET_ROWS:,} "
            "(set LAB_MAX_ROWS to accept more)"
        )
    distinct_ids, null_ids = con.execute(
        f"SELECT count(DISTINCT row_id), count(*) FILTER (WHERE row_id IS NULL) FROM {source}"
    ).fetchone()
    if null_ids:
        raise DatasetError(f"{side}: row_id must not be NULL ({null_ids} rows)")
    if total != distinct_ids:
        raise DatasetError(f"{side}: row_id must be unique within its side ({total - distinct_ids} duplicates)")
    empty_rows, empty_null_rows = con.execute(
        f"SELECT count(*) FILTER (WHERE {EMPTY_KEYS_SQL}), "
        f"count(*) FILTER (WHERE join_key IS NULL) FROM {source}"
    ).fetchone()
    distinct_keys = con.execute(
        f"SELECT count(DISTINCT join_key) FROM {source} WHERE {NONEMPTY_SQL}"
    ).fetchone()[0]
    return {
        "rows": int(total),
        "emptyRows": int(empty_rows),
        "emptyNullRows": int(empty_null_rows),
        "distinctKeys": int(distinct_keys),
        "bytes": path.stat().st_size,
    }


def _key_counts(con: duckdb.DuckDBPyConnection, path: Path) -> dict[str, int]:
    return {
        str(key): int(count)
        for key, count in con.execute(
            f"SELECT join_key, count(*) FROM read_parquet({sql_string(path)}) "
            f"WHERE {NONEMPTY_SQL} GROUP BY 1"
        ).fetchall()
    }


def _preview(con: duckdb.DuckDBPyConnection, path: Path) -> list[dict]:
    source = f"read_parquet({sql_string(path)})"
    rows = con.execute(
        f"(SELECT * FROM {source} WHERE {NONEMPTY_SQL} ORDER BY row_id LIMIT 6) "
        f"UNION ALL (SELECT * FROM {source} WHERE {EMPTY_KEYS_SQL} ORDER BY row_id LIMIT 4)"
    ).fetchall()
    return [
        {"rowId": int(row_id), "joinKey": key, "isNull": key is None, "payload": payload}
        for row_id, key, payload in rows
    ]


def profile_pair(con: duckdb.DuckDBPyConnection, left_path: Path, right_path: Path) -> dict:
    left = _read_side(con, left_path, "left")
    right = _read_side(con, right_path, "right")
    left_counts = _key_counts(con, left_path)
    right_counts = _key_counts(con, right_path)
    all_keys = set(left_counts) | set(right_counts)
    key_rows = [
        {
            "key": key,
            "leftRows": left_counts.get(key, 0),
            "rightRows": right_counts.get(key, 0),
            "pairs": left_counts.get(key, 0) * right_counts.get(key, 0),
        }
        for key in all_keys
    ]
    hot = [row for row in key_rows if max(row["leftRows"], row["rightRows"]) >= HOT_FREQUENCY_THRESHOLD]
    hot.sort(key=lambda row: (max(row["leftRows"], row["rightRows"]), row["pairs"]), reverse=True)
    top_keys = sorted(
        key_rows, key=lambda row: (max(row["leftRows"], row["rightRows"]), row["pairs"]), reverse=True
    )[:20]
    pairs = sum(
        left_counts[key] * right_counts[key] for key in left_counts if right_counts.get(key, 0) > 0
    )
    return {
        "left": left,
        "right": right,
        "keys": {
            "distinct": len(all_keys),
            "onlyLeft": sum(1 for key in left_counts if key not in right_counts),
            "onlyRight": sum(1 for key in right_counts if key not in left_counts),
            "matchablePairs": pairs,
            "hotCount": len(hot),
            "threshold": HOT_FREQUENCY_THRESHOLD,
        },
        "hotKeys": hot,
        "topKeys": top_keys,
        "preview": {"left": _preview(con, left_path), "right": _preview(con, right_path)},
    }


# --------------------------------------------------------------------------- #
# Creating datasets
# --------------------------------------------------------------------------- #


def _write_meta(con: duckdb.DuckDBPyConnection, dataset_id: str, name: str) -> dict:
    root = dataset_root(dataset_id)
    profile = profile_pair(con, root / "left.parquet", root / "right.parquet")
    meta = {"id": dataset_id, "name": name, "createdAt": int(time.time() * 1000), "profile": profile}
    (root / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


def create_sample_dataset() -> dict:
    dataset_id = "sample"
    root = DATA_ROOT / "datasets" / dataset_id
    root.mkdir(parents=True, exist_ok=True)

    def table(rows: list[tuple[int, str | None, str]]) -> pa.Table:
        return pa.table(
            {
                "row_id": pa.array([row[0] for row in rows], type=pa.int64()),
                "join_key": pa.array([row[1] for row in rows], type=pa.string()),
                "payload": pa.array([row[2] for row in rows], type=pa.string()),
            }
        )

    pq.write_table(table(SAMPLE_LEFT_ROWS), root / "left.parquet", compression="snappy")
    pq.write_table(table(SAMPLE_RIGHT_ROWS), root / "right.parquet", compression="snappy")
    con = duckdb.connect()
    try:
        return _write_meta(
            con,
            dataset_id,
            "Built-in sample: two hot keys, ordinary keys, repeated payloads and empty keys",
        )
    finally:
        con.close()


def _schema_problem(path: Path, side: str) -> str | None:
    try:
        schema = pq.read_schema(path)
    except Exception as error:  # noqa: BLE001 - any parse failure means unusable input
        return f"{side}: not a readable Parquet file ({error})"
    if "row_id" not in schema.names or "join_key" not in schema.names:
        return f"{side}: required columns are row_id and join_key (payload optional)"
    if not pa.types.is_integer(schema.field("row_id").type):
        return f"{side}: row_id must be an integer, got {schema.field('row_id').type}"
    join_type = schema.field("join_key").type
    if not (pa.types.is_string(join_type) or pa.types.is_large_string(join_type)):
        return f"{side}: join_key must be a string, got {join_type}"
    return None


def _normalise(source_path: Path, target_path: Path) -> None:
    schema = pq.read_schema(source_path)
    columns = ["row_id", "join_key"] + (["payload"] if "payload" in schema.names else [])
    table = pq.read_table(source_path, columns=columns)
    if "payload" not in table.column_names:
        table = table.append_column("payload", pa.array([""] * table.num_rows, type=pa.string()))
    table = table.select(["row_id", "join_key", "payload"]).cast(CANONICAL_SCHEMA)
    pq.write_table(table, target_path, compression="snappy")


def create_uploaded_dataset(left_bytes_path: Path, right_bytes_path: Path, dataset_id: str) -> dict:
    root = DATA_ROOT / "datasets" / dataset_id
    root.mkdir(parents=True, exist_ok=True)
    for source, side in ((left_bytes_path, "left"), (right_bytes_path, "right")):
        problem = _schema_problem(source, side)
        if problem:
            raise DatasetError(problem)
    try:
        _normalise(left_bytes_path, root / "left.parquet")
        _normalise(right_bytes_path, root / "right.parquet")
    except (pa.ArrowException, ValueError) as error:
        raise DatasetError(f"could not normalise upload: {error}") from error
    con = duckdb.connect()
    try:
        return _write_meta(
            con,
            dataset_id,
            f"Uploaded pair ({left_bytes_path.name} / {right_bytes_path.name})",
        )
    finally:
        con.close()


def load_meta(dataset_id: str) -> dict:
    meta_path = dataset_root(dataset_id) / "meta.json"
    if not meta_path.is_file():
        raise DatasetError(f"dataset {dataset_id!r} has no metadata")
    return json.loads(meta_path.read_text())


def dataset_paths(dataset_id: str) -> tuple[Path, Path]:
    root = dataset_root(dataset_id)
    return root / "left.parquet", root / "right.parquet"


# --------------------------------------------------------------------------- #
# Key search
# --------------------------------------------------------------------------- #


def search_keys(dataset_id: str, query: str = "", limit: int = KEY_PAGE_SIZE, sort: str = "hot") -> dict:
    left_path, right_path = dataset_paths(dataset_id)
    limit = max(1, min(limit, 1000))
    con = duckdb.connect()
    try:
        left = _key_counts(con, left_path)
        right = _key_counts(con, right_path)
        empty_left, empty_right = con.execute(
            f"SELECT (SELECT count(*) FROM read_parquet({sql_string(left_path)}) WHERE {EMPTY_KEYS_SQL}), "
            f"(SELECT count(*) FROM read_parquet({sql_string(right_path)}) WHERE {EMPTY_KEYS_SQL})"
        ).fetchone()
    finally:
        con.close()
    needle = query.strip().lower()
    rows = []
    for key in set(left) | set(right):
        if needle and needle not in key.lower():
            continue
        left_rows = left.get(key, 0)
        right_rows = right.get(key, 0)
        rows.append(
            {
                "key": key,
                "leftRows": left_rows,
                "rightRows": right_rows,
                "pairs": left_rows * right_rows,
                "hot": max(left_rows, right_rows) >= HOT_FREQUENCY_THRESHOLD,
            }
        )
    if sort == "pairs":
        rows.sort(key=lambda row: (row["pairs"], max(row["leftRows"], row["rightRows"]), row["key"]), reverse=True)
    else:
        rows.sort(key=lambda row: (max(row["leftRows"], row["rightRows"]), row["pairs"], row["key"]), reverse=True)
    return {
        "keys": rows[:limit],
        "total": len(rows),
        "limit": limit,
        "empty": {
            "leftRows": int(empty_left),
            "rightRows": int(empty_right),
            "note": "NULL 与空字符串按 SQL 规则互不匹配；单独统计，并在关联前排除",
        },
        "threshold": HOT_FREQUENCY_THRESHOLD,
    }
