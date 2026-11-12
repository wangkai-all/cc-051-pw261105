"""Layout planner and the physical Parquet writer.

Ordinary layout
    Both sides use ``hash(join_key) % P``. A hot key lands in one partition per
    side, duplicates included.

Salted layout
    For each chosen hot key, left rows are spread over ``S`` buckets by
    ``hash(row_id) % S``; every right row of that key is copied to all ``S``
    buckets. Bucket ``b`` is joined only against bucket ``b`` of the other side,
    so each initially joinable pair occurs exactly once. Non-salted keys take the
    ordinary layout *inside* one salt bucket (``hash(key) % S``) and one of
    ``ceil(P / S)`` ordinary partitions there, so salting never separates
    partners.

Empty keys (``NULL`` / ``''``) never match and are excluded from the join on both
sides. A hot key can never be empty, so empty rows follow the ordinary layout in
partition 0 and are counted separately throughout.

Trial numbers are exact: every per-key bucket placement comes from the same
``hash`` expression the writer uses, not from an even-split heuristic. The per-key
layout map is fetched with one SQL query over the distinct keys.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from .dataset import sql_string
from .sample_data import (
    EMPTY_KEYS_SQL,
    HOT_FREQUENCY_THRESHOLD,
    MAX_HOT_CHOICES,
    MAX_PLAN_PARTITIONS,
    MAX_SALT,
    MIN_SALT,
    NONEMPTY_SQL,
)


class PlanError(ValueError):
    """Invalid trial or execution parameters."""


@dataclass
class HotKeyPlan:
    key: str
    left_rows: int
    right_rows: int
    salt: int
    left_by_bucket: list[int]

    @property
    def right_by_bucket(self) -> list[int]:
        """Every right row is copied into every salt bucket."""
        return [self.right_rows] * self.salt

    @property
    def rows_written(self) -> int:
        return sum(self.left_by_bucket) + sum(self.right_by_bucket)

    @property
    def rows_copied(self) -> int:
        """Extra right-side storage caused by replication."""
        return sum(self.right_by_bucket) - self.right_rows

    @property
    def output_rows(self) -> int:
        """Join rows for this key: left × right, exactly."""
        return self.left_rows * self.right_rows

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "leftRows": self.left_rows,
            "rightRows": self.right_rows,
            "salt": self.salt,
            "leftByBucket": self.left_by_bucket,
            "rightByBucket": self.right_by_bucket,
            "rowsCopied": self.rows_copied,
            "rowsWritten": self.rows_written,
            "outputRows": self.output_rows,
        }


@dataclass
class Plan:
    partitions: int
    salt: int
    hot: list[HotKeyPlan] = field(default_factory=list)

    @property
    def ordinary_partitions_per_salt(self) -> int:
        return math.ceil(self.partitions / self.salt) if self.hot else self.partitions

    @property
    def rows_copied(self) -> int:
        return sum(item.rows_copied for item in self.hot)

    def to_json(self) -> dict:
        return {
            "partitions": self.partitions,
            "salt": self.salt,
            "ordinaryPartitionsPerSalt": self.ordinary_partitions_per_salt,
            "hotKeys": [item.to_json() for item in self.hot],
        }


def validate_parameters(partitions: int, salt: int) -> None:
    if not 1 <= partitions <= MAX_PLAN_PARTITIONS:
        raise PlanError(f"partitions must be between 1 and {MAX_PLAN_PARTITIONS} (got {partitions})")
    if not MIN_SALT <= salt <= MAX_SALT:
        raise PlanError(f"salt buckets must be between {MIN_SALT} and {MAX_SALT} (got {salt})")


def _spread(total: int, buckets: int) -> list[int]:
    if total <= 0:
        return [0] * buckets
    base, remainder = divmod(total, buckets)
    return [base + (1 if index < remainder else 0) for index in range(buckets)]


def build_plan(
    *,
    partitions: int,
    salt: int,
    hot_keys: list[str],
    left_counts: dict[str, int],
    right_counts: dict[str, int],
    left_bucket_counts: dict[tuple[str, int], int] | None = None,
    require_hot: bool = True,
) -> Plan:
    """Materialised plan. ``require_hot=False`` replays a previously chosen plan."""
    validate_parameters(partitions, salt)
    if len(hot_keys) > MAX_HOT_CHOICES:
        raise PlanError(f"at most {MAX_HOT_CHOICES} hot keys can be salted (got {len(hot_keys)})")
    if len(set(hot_keys)) != len(hot_keys):
        raise PlanError("the same hot key is selected twice")
    plans: list[HotKeyPlan] = []
    for key in hot_keys:
        left_rows = int(left_counts.get(key, 0))
        right_rows = int(right_counts.get(key, 0))
        if left_rows == 0 and right_rows == 0:
            raise PlanError(f"hot key {key!r} does not exist in either table")
        if require_hot and max(left_rows, right_rows) < HOT_FREQUENCY_THRESHOLD:
            raise PlanError(
                f"key {key!r} is not hot: max side frequency {max(left_rows, right_rows)} "
                f"< threshold {HOT_FREQUENCY_THRESHOLD}"
            )
        plans.append(
            HotKeyPlan(
                key=key,
                left_rows=left_rows,
                right_rows=right_rows,
                salt=salt,
                left_by_bucket=(
                    _spread(left_rows, salt)
                    if left_bucket_counts is None
                    else [int(left_bucket_counts.get((key, bucket), 0)) for bucket in range(salt)]
                ),
            )
        )
    return Plan(partitions=partitions, salt=salt, hot=plans)


def exact_join_rows(left_counts: dict[str, int], right_counts: dict[str, int]) -> int:
    """Sum of left_freq × right_freq over shared non-empty keys."""
    return sum(left_counts[key] * right_counts[key] for key in left_counts if right_counts.get(key, 0) > 0)


# --------------------------------------------------------------------------- #
# Trial (no data written)
# --------------------------------------------------------------------------- #


@dataclass
class SideScan:
    counts: dict[str, int]
    empty_rows: int
    empty_null_rows: int
    salted_bucket_rows: dict[tuple[str, int], int]  # hash(row_id) % S for chosen hot keys
    total_bytes: int


def scan_side(
    con: duckdb.DuckDBPyConnection,
    path: Path,
    *,
    salt: int,
    hot_keys: list[str],
) -> SideScan:
    source = f"read_parquet({sql_string(path)})"
    counts = {
        str(key): int(count)
        for key, count in con.execute(
            f"SELECT join_key, count(*) FROM {source} WHERE {NONEMPTY_SQL} GROUP BY 1"
        ).fetchall()
    }
    empty_rows, empty_null_rows = con.execute(
        f"SELECT count(*) FILTER (WHERE {EMPTY_KEYS_SQL}), "
        f"count(*) FILTER (WHERE join_key IS NULL) FROM {source}"
    ).fetchone()
    key_list = "(" + ", ".join(sql_string(key) for key in hot_keys) + ")" if hot_keys else "(NULL)"
    salted_bucket_rows = {
        (str(key), int(bucket)): int(count)
        for key, bucket, count in con.execute(
            f"SELECT join_key, hash(row_id) % {salt} AS b, count(*) FROM {source} "
            f"WHERE join_key IN {key_list} GROUP BY 1, 2"
        ).fetchall()
    }
    return SideScan(
        counts=counts,
        empty_rows=int(empty_rows),
        empty_null_rows=int(empty_null_rows),
        salted_bucket_rows=salted_bucket_rows,
        total_bytes=path.stat().st_size,
    )


def key_layout(
    con: duckdb.DuckDBPyConnection,
    left_path: Path,
    right_path: Path,
    *,
    partitions: int,
    salt: int,
) -> dict[str, tuple[int, int]]:
    """One query: for every non-empty key, its ordinary partition and salt batch."""
    left_source = f"read_parquet({sql_string(left_path)})"
    right_source = f"read_parquet({sql_string(right_path)})"
    rows = con.execute(
        f"WITH k AS ("
        f"  SELECT join_key FROM {left_source} WHERE {NONEMPTY_SQL}"
        f"  UNION SELECT join_key FROM {right_source} WHERE {NONEMPTY_SQL}"
        f") SELECT join_key, hash(join_key) % {partitions} AS p, hash(join_key) % {salt} AS s FROM k"
    ).fetchall()
    return {str(key): (int(partition), int(batch)) for key, partition, batch in rows}


def trial(
    *,
    left_path: Path,
    right_path: Path,
    partitions: int,
    salt: int,
    hot_keys: list[str],
) -> dict:
    """Plan numbers shown before the user commits to a physical layout."""
    validate_parameters(partitions, salt)
    con = duckdb.connect()
    try:
        left_scan = scan_side(con, left_path, salt=salt, hot_keys=hot_keys)
        right_scan = scan_side(con, right_path, salt=salt, hot_keys=hot_keys)
        layout = key_layout(con, left_path, right_path, partitions=partitions, salt=salt)
    finally:
        con.close()
    plan = build_plan(
        partitions=partitions,
        salt=salt,
        hot_keys=hot_keys,
        left_counts=left_scan.counts,
        right_counts=right_scan.counts,
        left_bucket_counts=left_scan.salted_bucket_rows,
    )
    return summarize(plan, left_scan=left_scan, right_scan=right_scan, layout=layout)


def summarize(
    plan: Plan,
    *,
    left_scan: SideScan,
    right_scan: SideScan,
    layout: dict[str, tuple[int, int]],
) -> dict:
    """Full trial picture: buckets, largest bucket, replication, output rows."""
    left, right, salt = left_scan, right_scan, plan.salt
    salted_keys = {item.key for item in plan.hot}
    left_source_rows = sum(left.counts.values()) + left.empty_rows
    right_source_rows = sum(right.counts.values()) + right.empty_rows
    salted_left_rows = sum(left.counts.get(key, 0) for key in salted_keys)
    salted_right_rows = sum(right.counts.get(key, 0) for key in salted_keys)
    bytes_per_row = (left.total_bytes + right.total_bytes) / max(1, left_source_rows + right_source_rows)

    bucket_left: list[int] = []
    bucket_right: list[int] = []
    bucket_rows: list[int] = []
    bucket_files: list[dict] = []
    max_bucket: dict | None = None

    if plan.hot:
        # Every non-salted key belongs to exactly one batch; salted keys follow
        # their computed left bucket or the replicated right rows.
        bucket_left = [0] * salt
        bucket_right = [0] * salt
        for key, count in left.counts.items():
            if key not in salted_keys:
                bucket_left[layout[key][1]] += count
        for key, count in right.counts.items():
            if key not in salted_keys:
                bucket_right[layout[key][1]] += count
        for item in plan.hot:
            for bucket in range(salt):
                bucket_left[bucket] += item.left_by_bucket[bucket]
                bucket_right[bucket] += item.right_by_bucket[bucket]
        # Empty-key rows live once, in batch 0's first ordinary file.
        bucket_left[0] += left.empty_rows
        bucket_right[0] += right.empty_rows
        bucket_rows = [bucket_left[i] + bucket_right[i] for i in range(salt)]
        index = max(range(salt), key=lambda i: bucket_rows[i])
        max_bucket = {
            "batch": index,
            "leftRows": bucket_left[index],
            "rightRows": bucket_right[index],
            "rows": bucket_rows[index],
        }
        bucket_files = []
        for batch in range(salt):
            ordinary_left = sum(
                count for key, count in left.counts.items()
                if key not in salted_keys and layout[key][1] == batch
            ) + (left.empty_rows if batch == 0 else 0)
            ordinary_right = sum(
                count for key, count in right.counts.items()
                if key not in salted_keys and layout[key][1] == batch
            ) + (right.empty_rows if batch == 0 else 0)
            bucket_files.append(
                {
                    "batch": batch,
                    "saltedLeftRows": sum(item.left_by_bucket[batch] for item in plan.hot),
                    "saltedRightRows": sum(item.right_rows for item in plan.hot),
                    "ordinaryLeftRows": ordinary_left,
                    "ordinaryRightRows": ordinary_right,
                    "ordinaryPartitions": plan.ordinary_partitions_per_salt,
                    "leftRows": bucket_left[batch],
                    "rightRows": bucket_right[batch],
                    "rows": bucket_rows[batch],
                }
            )

    # Baseline: the plain hash layout over every shared key, plus empty rows in
    # partition 0. This is what the reader would face without salting.
    partition_left: dict[int, int] = {}
    partition_right: dict[int, int] = {}
    partition_keys: dict[int, list[str]] = {}
    for key in left.counts:
        if right.counts.get(key, 0) > 0:
            partition = layout[key][0]
            partition_left[partition] = partition_left.get(partition, 0) + left.counts[key]
            partition_right[partition] = partition_right.get(partition, 0) + right.counts[key]
            partition_keys.setdefault(partition, []).append(key)
    partition_left[0] = partition_left.get(0, 0) + left.empty_rows
    partition_right[0] = partition_right.get(0, 0) + right.empty_rows
    partition_keys.setdefault(0, [])
    index = max(
        set(partition_left) | set(partition_right),
        key=lambda p: partition_left.get(p, 0) + partition_right.get(p, 0),
    )
    max_ordinary = {
        "partition": index,
        "leftRows": partition_left.get(index, 0),
        "rightRows": partition_right.get(index, 0),
        "rows": partition_left.get(index, 0) + partition_right.get(index, 0),
        "keys": sorted(partition_keys.get(index, [])),
    }

    rows_written = sum(bucket_rows) if bucket_rows else left_source_rows + right_source_rows
    copied_rows = sum(item.rows_copied for item in plan.hot)
    return {
        "partitions": plan.partitions,
        "salt": plan.salt,
        "salted": bool(plan.hot),
        "ordinaryPartitionsPerSalt": plan.ordinary_partitions_per_salt,
        "bucketCount": len(bucket_rows),
        "bucketRows": bucket_rows,
        "bucketLeftRows": bucket_left,
        "bucketRightRows": bucket_right,
        "bucketFiles": bucket_files,
        "maxBucket": max_bucket,
        "maxOrdinaryPartition": max_ordinary,
        "rowsRead": left_source_rows + right_source_rows,
        "rowsWritten": rows_written,
        "rowsCopied": copied_rows,
        "bytesCopied": int(copied_rows * bytes_per_row),
        "bytePerRow": bytes_per_row,
        "replicationFactor": rows_written / max(1, left_source_rows + right_source_rows),
        "saltedLeftRows": salted_left_rows,
        "saltedRightRows": salted_right_rows,
        "saltedRowsWritten": salted_left_rows + salted_right_rows * salt,
        "estimatedJoinRows": exact_join_rows(left.counts, right.counts),
        "saltedOutputRows": sum(item.output_rows for item in plan.hot),
        "hotKeys": [item.to_json() for item in plan.hot],
        "empty": {
            "leftRows": left.empty_rows,
            "rightRows": right.empty_rows,
            "leftNullRows": left.empty_null_rows,
            "rightNullRows": right.empty_null_rows,
            "excludedFromJoin": True,
        },
        "plan": plan.to_json(),
    }


# --------------------------------------------------------------------------- #
# Physical writing
# --------------------------------------------------------------------------- #


@dataclass
class BucketFile:
    dataset: str  # left | right
    batch: int    # salt batch index (0 when nothing is salted)
    scope: str    # salted | ordinary
    ordinal: int  # position of the ordinary partition inside its batch
    path: Path
    rows: int

    def to_json(self) -> dict:
        return {
            "dataset": self.dataset,
            "batch": self.batch,
            "scope": self.scope,
            "ordinal": self.ordinal,
            "file": self.path.name,
            "rows": self.rows,
            "bytes": self.path.stat().st_size,
        }


def write_buckets(
    con: duckdb.DuckDBPyConnection,
    *,
    plan: Plan,
    left_path: Path,
    right_path: Path,
    out_dir: Path,
) -> list[BucketFile]:
    """Materialise the plan as Parquet, one left/right pair per batch.

    Salted hot keys get one file per salt batch on each side. Everything else —
    ordinary keys and empty keys — gets ``ceil(P/S)`` ordinary files inside a
    single batch, so no row is ever written twice. With no hot keys the ordinary
    layout's ``P`` partitions are written directly.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.parquet"):
        stale.unlink()

    left_source = f"read_parquet({sql_string(left_path)})"
    right_source = f"read_parquet({sql_string(right_path)})"
    salted = [item.key for item in plan.hot]
    key_list = "(" + ", ".join(sql_string(key) for key in salted) + ")" if salted else "(NULL)"
    files: list[BucketFile] = []

    def copy_bucket(query: str, name: str, dataset: str, batch: int, scope: str, ordinal: int) -> None:
        path = out_dir / name
        con.execute(f"COPY ({query}) TO {sql_string(path)} (FORMAT PARQUET)")
        rows = int(con.execute(f"SELECT count(*) FROM read_parquet({sql_string(path)})").fetchone()[0])
        files.append(BucketFile(dataset, batch, scope, ordinal, path, rows))

    if not plan.hot:
        for ordinal in range(plan.partitions):
            condition = f"hash(join_key) % {plan.partitions} = {ordinal} AND {NONEMPTY_SQL}"
            if ordinal == 0:
                condition = f"({condition}) OR {EMPTY_KEYS_SQL}"
            for dataset, source in (("left", left_source), ("right", right_source)):
                copy_bucket(
                    f"SELECT row_id, join_key, payload FROM {source} WHERE {condition}",
                    f"{dataset}-{ordinal + 1:02d}.parquet",
                    dataset, 0, "ordinary", ordinal,
                )
        return files

    per_batch = plan.ordinary_partitions_per_salt
    empty_written = {"left": False, "right": False}
    for batch in range(plan.salt):
        for dataset, source in (("left", left_source), ("right", right_source)):
            # Salted rows: the left side is spread by row id, the right side is
            # replicated into every batch.
            salted_where = (
                f"join_key IN {key_list} AND hash(row_id) % {plan.salt} = {batch}"
                if dataset == "left"
                else f"join_key IN {key_list}"
            )
            copy_bucket(
                f"SELECT row_id, join_key, payload FROM {source} WHERE {salted_where}",
                f"{dataset}-salted-{batch + 1:02d}.parquet",
                dataset, batch, "salted", 0,
            )
            # Ordinary rows: one batch by key hash, then one of ceil(P/S)
            # partitions by key hash.
            for ordinal in range(per_batch):
                ordinary_where = (
                    f"join_key NOT IN {key_list} AND {NONEMPTY_SQL} "
                    f"AND hash(join_key) % {plan.salt} = {batch} "
                    f"AND hash(join_key) % {per_batch} = {ordinal}"
                )
                empty_where = "" if empty_written[dataset] else f" OR {EMPTY_KEYS_SQL}"
                copy_bucket(
                    f"SELECT row_id, join_key, payload FROM {source} WHERE ({ordinary_where}){empty_where}",
                    f"{dataset}-ordinary-{batch + 1:02d}-{ordinal + 1:02d}.parquet",
                    dataset, batch, "ordinary", ordinal,
                )
                empty_written[dataset] = True
    return files
