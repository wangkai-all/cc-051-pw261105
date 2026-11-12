"""Physical execution: write bucket Parquet, join bucket by bucket, verify.

The join unit is a *batch*:

* salted plan — batch ``b`` holds the salted file for the chosen hot keys in
  bucket ``b`` plus every ordinary file of that batch. Left batch ``b`` is joined
  only with right batch ``b``, so replicated right rows never pair with each
  other and every original pair appears exactly once.
* ordinary plan — one batch with ``P`` ordinary files; partition ``i`` is joined
  against partition ``i``.

Verification compares the result against a direct DuckDB equi-inner join on
``(left_row_id, right_row_id)`` pairs in both directions, plus row counts. That
proves both directions at once: no pair is missing, no pair is duplicated, and
duplicate multiplicity matches the baseline exactly.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .dataset import sql_string
from .plan import BucketFile, Plan, write_buckets
from .sample_data import EMPTY_KEYS_SQL

RESULT_SCHEMA = (
    "left_row_id BIGINT, right_row_id BIGINT, join_key VARCHAR, "
    "left_payload VARCHAR, right_payload VARCHAR"
)
RESULT_COLUMNS = "left_row_id, right_row_id, join_key, left_payload, right_payload"


@dataclass
class ExecutionOutcome:
    files: list[BucketFile]
    batches: list[dict]
    result_rows: int
    write_seconds: float
    join_seconds: float
    verification: dict
    storage_bytes: int
    result_parquet_bytes: int


def _read_list(con: duckdb.DuckDBPyConnection, paths: list[Path]) -> str:
    if not paths:
        return "(SELECT NULL::BIGINT AS row_id, NULL::VARCHAR AS join_key, NULL::VARCHAR AS payload WHERE FALSE)"
    if len(paths) == 1:
        return f"read_parquet({sql_string(paths[0])})"
    return "read_parquet([" + ", ".join(sql_string(path) for path in paths) + "])"


def _files_for(files: list[BucketFile], dataset: str, batch: int) -> list[Path]:
    return [file.path for file in files if file.dataset == dataset and file.batch == batch]


def _join_batch(
    con: duckdb.DuckDBPyConnection,
    left_paths: list[Path],
    right_paths: list[Path],
) -> None:
    """Join one batch into temp table ``_bucket_out``."""
    left_read = _read_list(con, left_paths)
    right_read = _read_list(con, right_paths)
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE _bucket_out AS "
        f"SELECT l.row_id AS left_row_id, r.row_id AS right_row_id, l.join_key AS join_key, "
        f"l.payload AS left_payload, r.payload AS right_payload "
        f"FROM {left_read} l INNER JOIN {right_read} r ON l.join_key = r.join_key "
        f"WHERE l.join_key IS NOT NULL AND l.join_key <> '' "
        f"AND r.join_key IS NOT NULL AND r.join_key <> ''"
    )


def execute(*, plan: Plan, left_path: Path, right_path: Path, work_dir: Path) -> ExecutionOutcome:
    """Write partitioned Parquet, execute bucket joins, verify against baseline."""
    con = duckdb.connect()
    try:
        write_started = time.perf_counter()
        files = write_buckets(
            con, plan=plan, left_path=left_path, right_path=right_path, out_dir=work_dir / "buckets"
        )
        write_seconds = time.perf_counter() - write_started

        con.execute(f"CREATE OR REPLACE TABLE _result ({RESULT_SCHEMA})")
        batches: list[dict] = []
        join_started = time.perf_counter()
        for batch in sorted({file.batch for file in files}):
            left_paths = _files_for(files, "left", batch)
            right_paths = _files_for(files, "right", batch)
            _join_batch(con, left_paths, right_paths)
            con.execute(f"INSERT INTO _result SELECT {RESULT_COLUMNS} FROM _bucket_out")
            batches.append(
                {
                    "batch": batch,
                    "leftFiles": len(left_paths),
                    "rightFiles": len(right_paths),
                    "leftRows": _count_rows(con, left_paths),
                    "rightRows": _count_rows(con, right_paths),
                    "joinRows": int(con.execute("SELECT count(*) FROM _bucket_out").fetchone()[0]),
                }
            )
        join_seconds = time.perf_counter() - join_started

        result_path = work_dir / "result.parquet"
        con.execute(
            f"COPY (SELECT * FROM _result ORDER BY left_row_id, right_row_id) "
            f"TO {sql_string(result_path)} (FORMAT PARQUET)"
        )
        result_rows = int(con.execute("SELECT count(*) FROM _result").fetchone()[0])
        verification = _verify(
            con,
            plan=plan,
            left_path=left_path,
            right_path=right_path,
            files=files,
            result_rows=result_rows,
        )
        storage_bytes = sum(file.path.stat().st_size for file in files)
        result_parquet_bytes = result_path.stat().st_size
    finally:
        con.close()

    return ExecutionOutcome(
        files=files,
        batches=batches,
        result_rows=result_rows,
        write_seconds=round(write_seconds, 4),
        join_seconds=round(join_seconds, 4),
        verification=verification,
        storage_bytes=storage_bytes,
        result_parquet_bytes=result_parquet_bytes,
    )


def _count_rows(con: duckdb.DuckDBPyConnection, paths: list[Path]) -> int:
    return int(con.execute(f"SELECT count(*) FROM {_read_list(con, paths)}").fetchone()[0])


def _verify(
    con: duckdb.DuckDBPyConnection,
    *,
    plan: Plan,
    left_path: Path,
    right_path: Path,
    files: list[BucketFile],
    result_rows: int,
) -> dict:
    """Compare the bucket result to a direct DuckDB join, pair by pair."""
    left_source = f"read_parquet({sql_string(left_path)})"
    right_source = f"read_parquet({sql_string(right_path)})"
    empty_left, empty_right = con.execute(
        f"SELECT (SELECT count(*) FROM {left_source} WHERE {EMPTY_KEYS_SQL}), "
        f"(SELECT count(*) FROM {right_source} WHERE {EMPTY_KEYS_SQL})"
    ).fetchone()

    con.execute(
        f"CREATE OR REPLACE TEMP TABLE _baseline AS "
        f"SELECT l.row_id AS left_row_id, r.row_id AS right_row_id "
        f"FROM {left_source} l INNER JOIN {right_source} r ON l.join_key = r.join_key "
        f"WHERE l.join_key IS NOT NULL AND l.join_key <> '' "
        f"AND r.join_key IS NOT NULL AND r.join_key <> ''"
    )
    baseline_rows = int(con.execute("SELECT count(*) FROM _baseline").fetchone()[0])
    missing = int(
        con.execute(
            "SELECT count(*) FROM (SELECT * FROM _baseline EXCEPT SELECT left_row_id, right_row_id FROM _result)"
        ).fetchone()[0]
    )
    extra = int(
        con.execute(
            "SELECT count(*) FROM (SELECT left_row_id, right_row_id FROM _result EXCEPT SELECT * FROM _baseline)"
        ).fetchone()[0]
    )
    duplicate_pairs = int(
        con.execute(
            "SELECT count(*) FROM (SELECT left_row_id, right_row_id, count(*) AS copies "
            "FROM _result GROUP BY 1, 2 HAVING copies > 1)"
        ).fetchone()[0]
    )
    empty_leak = int(
        con.execute("SELECT count(*) FROM _result WHERE join_key IS NULL OR join_key = ''").fetchone()[0]
    )

    left_written = sum(file.rows for file in files if file.dataset == "left")
    right_written = sum(file.rows for file in files if file.dataset == "right")
    left_read = int(con.execute(f"SELECT count(*) FROM {left_source}").fetchone()[0])
    right_read = int(con.execute(f"SELECT count(*) FROM {right_source}").fetchone()[0])
    replication = plan.rows_copied

    passed = (
        baseline_rows == result_rows
        and missing == 0
        and extra == 0
        and duplicate_pairs == 0
        and empty_leak == 0
        and left_written == left_read
        and right_written == right_read + replication
    )
    return {
        "passed": passed,
        "baselineRows": baseline_rows,
        "bucketRows": result_rows,
        "rowsMatch": baseline_rows == result_rows,
        "missingPairs": missing,
        "extraPairs": extra,
        "duplicatePairs": duplicate_pairs,
        "emptyKeyLeaks": empty_leak,
        "left": {
            "sourceRows": left_read,
            "rowsWritten": left_written,
            "lostRows": left_read - left_written,
        },
        "right": {
            "sourceRows": right_read,
            "rowsWritten": right_written,
            "replicatedRows": replication,
            "lostRows": right_read + replication - right_written,
        },
        "empty": {
            "leftRows": int(empty_left),
            "rightRows": int(empty_right),
            "excludedFromJoin": True,
        },
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
