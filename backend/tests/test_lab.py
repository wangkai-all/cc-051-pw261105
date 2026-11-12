"""Tests for the hot-key plan lab against DuckDB's own join semantics."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import dataset, engine, keys  # noqa: E402
from app.plan import PlanError, build_plan, trial  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def sample() -> dict:
    return dataset.create_sample_dataset()


@pytest.fixture(scope="module")
def paths() -> tuple[Path, Path]:
    return dataset.dataset_paths("sample")


@pytest.fixture(scope="module")
def counts(paths: tuple[Path, Path]) -> tuple[dict, dict]:
    con = duckdb.connect()
    try:
        return dataset._key_counts(con, paths[0]), dataset._key_counts(con, paths[1])
    finally:
        con.close()


def _plan(portions: int, salt: int, keys: list[str], counts) -> build_plan:
    return build_plan(
        partitions=portions,
        salt=salt,
        hot_keys=keys,
        left_counts=counts[0],
        right_counts=counts[1],
        require_hot=False,
    )


def test_sample_has_hot_keys_and_empty_rows(sample: dict) -> None:
    profile = sample["profile"]
    assert profile["keys"]["hotCount"] == 2
    assert [item["key"] for item in profile["hotKeys"]] == ["HOT-VIP", "HOT-API"]
    assert profile["left"]["emptyRows"] == 10
    assert profile["left"]["emptyNullRows"] == 6
    assert profile["right"]["emptyRows"] == 5
    assert profile["keys"]["matchablePairs"] == 12807


def test_trial_matches_execution_bucket_rows(paths, counts, tmp_path_factory) -> None:
    for partitions, salt, keys in ((8, 4, ["HOT-VIP", "HOT-API"]), (8, 2, ["HOT-VIP"])):
        trial_result = trial(
            left_path=paths[0], right_path=paths[1], partitions=partitions, salt=salt, hot_keys=keys
        )
        plan = _plan(partitions, salt, keys, counts)
        outcome = engine.execute(
            plan=plan,
            left_path=paths[0],
            right_path=paths[1],
            work_dir=tmp_path_factory.mktemp("bucket"),
        )
        executed = [row["leftRows"] + row["rightRows"] for row in outcome.batches]
        assert executed == trial_result["bucketRows"]
        assert len(executed) == salt


def test_unsalted_trial_baseline_matches_execution(paths, counts, tmp_path_factory) -> None:
    trial_result = trial(left_path=paths[0], right_path=paths[1], partitions=8, salt=2, hot_keys=[])
    plan = _plan(8, 2, [], counts)
    outcome = engine.execute(
        plan=plan, left_path=paths[0], right_path=paths[1], work_dir=tmp_path_factory.mktemp("ordinary")
    )
    assert trial_result["salted"] is False
    assert trial_result["maxOrdinaryPartition"]["rows"] == 258
    # The whole dataset lives in one batch when nothing is salted, and every
    # partition file was written exactly once.
    assert len(outcome.batches) == 1
    assert trial_result["rowsWritten"] == sum(file.rows for file in outcome.files)
    assert outcome.result_rows == trial_result["estimatedJoinRows"]


def test_salted_result_matches_duckdb_exactly(paths, counts, tmp_path_factory) -> None:
    plan = _plan(8, 4, ["HOT-VIP", "HOT-API"], counts)
    outcome = engine.execute(
        plan=plan,
        left_path=paths[0],
        right_path=paths[1],
        work_dir=tmp_path_factory.mktemp("bucket"),
    )
    verification = outcome.verification
    assert verification["passed"] is True
    assert verification["missingPairs"] == 0
    assert verification["extraPairs"] == 0
    assert verification["duplicatePairs"] == 0
    assert verification["emptyKeyLeaks"] == 0
    assert verification["right"]["replicatedRows"] == plan.rows_copied
    assert outcome.result_rows == 12807


def test_hot_key_detail_accounts_for_every_row(paths, counts, tmp_path) -> None:
    plan = _plan(8, 4, ["HOT-VIP"], counts)
    work_dir = tmp_path / "job"
    outcome = engine.execute(plan=plan, left_path=paths[0], right_path=paths[1], work_dir=work_dir)
    detail = keys.key_breakdown(
        work_dir=work_dir, left_path=paths[0], right_path=paths[1], key="HOT-VIP"
    )
    assert detail["mode"] == "salted"
    assert detail["left"]["sourceRows"] == 180
    assert detail["left"]["lostRows"] == 0
    assert detail["left"]["rowsWritten"] == 180
    assert [item["batch"] for item in detail["left"]["buckets"]] == [0, 1, 2, 3]
    all_ids = [row for bucket in detail["left"]["buckets"] for row in bucket["rowIds"]]
    assert sorted(all_ids) == list(range(1, 181))  # row ids 1..180, each exactly once
    assert detail["left"]["duplicateWrites"] == 0
    assert detail["right"]["copiesWritten"] == 180  # 60 rows × 4 buckets − 60 originals
    assert [item["rows"] for item in detail["right"]["buckets"]] == [60, 60, 60, 60]
    assert detail["join"]["rows"] == 10800
    assert detail["join"]["matchesLeftTimesRight"] is True
    assert [row["batch"] for row in detail["join"]["perBatch"]] == [0, 1, 2, 3]
    assert sum(row["joinRows"] for row in detail["join"]["perBatch"]) == 10800
    assert outcome.result_rows == 12807


def test_empty_key_is_counted_and_never_joined(paths, counts, tmp_path) -> None:
    plan = _plan(8, 4, ["HOT-VIP"], counts)
    work_dir = tmp_path / "job-empty"
    engine.execute(plan=plan, left_path=paths[0], right_path=paths[1], work_dir=work_dir)
    detail = keys.key_breakdown(work_dir=work_dir, left_path=paths[0], right_path=paths[1], key="__empty__")
    assert detail["empty"] is True
    assert detail["keyLabel"] == "空键（NULL 与 空字符串）"
    assert detail["left"]["sourceRows"] == 10   # 6 NULL + 4 ''
    assert detail["right"]["sourceRows"] == 5   # 3 NULL + 2 ''
    assert detail["left"]["lostRows"] == 0
    assert detail["right"]["rowsWritten"] == 5
    assert detail["left"]["duplicateWrites"] == 0
    assert detail["join"]["rows"] == 0
    assert detail["join"]["expectedRows"] == 0


def test_non_hot_key_is_not_salted(paths, counts) -> None:
    with pytest.raises(PlanError, match="not hot"):
        build_plan(
            partitions=8,
            salt=4,
            hot_keys=["CUST-1001"],
            left_counts=counts[0],
            right_counts=counts[1],
        )


def test_hot_key_present_on_one_side_only(tmp_path_factory) -> None:
    """A key only the left side has is salted without replicating anything."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    root = tmp_path_factory.mktemp("one-side")
    left = root / "left.parquet"
    right = root / "right.parquet"
    pq.write_table(
        pa.table(
            {
                "row_id": list(range(1, 41)),
                "join_key": ["LEFT-ONLY"] * 30 + ["K1"] * 10,
                "payload": ["p"] * 40,
            }
        ),
        left,
    )
    pq.write_table(
        pa.table({"row_id": list(range(1, 6)), "join_key": ["K1"] * 5, "payload": ["q"] * 5}),
        right,
    )
    con = duckdb.connect()
    try:
        left_counts = dataset._key_counts(con, left)
        right_counts = dataset._key_counts(con, right)
    finally:
        con.close()
    plan = build_plan(
        partitions=8, salt=4, hot_keys=["LEFT-ONLY"],
        left_counts=left_counts, right_counts=right_counts, require_hot=False,
    )
    assert plan.hot[0].rows_copied == 0
    assert plan.hot[0].output_rows == 0
    outcome = engine.execute(
        plan=plan, left_path=left, right_path=right, work_dir=root / "job"
    )
    assert outcome.result_rows == 50
    assert outcome.verification["passed"] is True
    assert outcome.verification["left"]["rowsWritten"] == 40
    assert outcome.verification["right"]["rowsWritten"] == 5


def test_unsalted_baseline_is_the_skew(paths, counts) -> None:
    result = trial(
        left_path=paths[0], right_path=paths[1], partitions=8, salt=4, hot_keys=[]
    )
    assert result["salted"] is False
    assert result["maxOrdinaryPartition"]["rows"] == 258
    assert "HOT-VIP" in result["maxOrdinaryPartition"]["keys"]
    assert result["rowsCopied"] == 0


def test_upload_validation_rejects_duplicate_row_ids(tmp_path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    duplicate = tmp_path / "dup.parquet"
    pq.write_table(
        pa.table({"row_id": [1, 1], "join_key": ["a", "b"], "payload": ["x", "y"]}), duplicate
    )
    good = tmp_path / "good.parquet"
    pq.write_table(pa.table({"row_id": [1], "join_key": ["a"], "payload": ["x"]}), good)
    with pytest.raises(dataset.DatasetError, match="unique"):
        dataset.create_uploaded_dataset(duplicate, good, "test-dup")
