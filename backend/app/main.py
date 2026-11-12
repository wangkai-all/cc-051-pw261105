"""FastAPI app: datasets, key exploration, plan trials, execution and diagnostics."""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from pathlib import Path

import duckdb
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import dataset, engine, keys
from .dataset import DATA_ROOT, DatasetError, dataset_paths, load_meta, sql_string
from .plan import PlanError, build_plan, trial, validate_parameters
from .sample_data import MAX_HOT_CHOICES, MAX_PLAN_PARTITIONS, MAX_SALT, MIN_SALT

app = FastAPI(title="Hot-key partition lab", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS_ROOT = DATA_ROOT / "jobs"
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


class PlanRequest(BaseModel):
    datasetId: str
    partitions: int = Field(default=8, ge=1, le=MAX_PLAN_PARTITIONS)
    salt: int = Field(default=4, ge=MIN_SALT, le=MAX_SALT)
    hotKeys: list[str] = Field(default_factory=list, max_length=MAX_HOT_CHOICES)


class CompareRequest(BaseModel):
    datasetId: str
    partitions: int = Field(default=8, ge=1, le=MAX_PLAN_PARTITIONS)
    hotKeys: list[str] = Field(default_factory=list, max_length=MAX_HOT_CHOICES)
    salts: list[int] = Field(default_factory=lambda: [2, 4, 8])


@app.on_event("startup")
def ensure_sample() -> None:
    if not (DATA_ROOT / "datasets" / "sample" / "meta.json").is_file():
        dataset.create_sample_dataset()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "dataRoot": str(DATA_ROOT)}


# --------------------------------------------------------------------------- #
# Datasets
# --------------------------------------------------------------------------- #


@app.get("/api/datasets")
def list_datasets() -> dict:
    items = []
    datasets_dir = DATA_ROOT / "datasets"
    if datasets_dir.is_dir():
        for meta_path in sorted(datasets_dir.glob("*/meta.json")):
            try:
                meta = load_meta(meta_path.parent.name)
            except (DatasetError, ValueError):
                continue
            items.append(
                {
                    "id": meta["id"],
                    "name": meta["name"],
                    "leftRows": meta["profile"]["left"]["rows"],
                    "rightRows": meta["profile"]["right"]["rows"],
                    "hotCount": meta["profile"]["keys"]["hotCount"],
                    "createdAt": meta.get("createdAt", 0),
                }
            )
    return {
        "datasets": items,
        "limits": {
            "maxPartitions": MAX_PLAN_PARTITIONS,
            "minSalt": MIN_SALT,
            "maxSalt": MAX_SALT,
            "maxHotKeys": MAX_HOT_CHOICES,
        },
    }


@app.get("/api/datasets/{dataset_id}")
def get_dataset(dataset_id: str) -> dict:
    try:
        return load_meta(dataset_id)
    except DatasetError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/api/datasets/sample")
def create_sample() -> dict:
    return dataset.create_sample_dataset()


@app.post("/api/datasets/upload")
async def upload_dataset(left: UploadFile = File(...), right: UploadFile = File(...)) -> dict:
    dataset_id = uuid.uuid4().hex[:12]
    staging = DATA_ROOT / "uploads" / dataset_id
    staging.mkdir(parents=True, exist_ok=True)
    staged_left = staging / (left.filename or "left.parquet")
    staged_right = staging / (right.filename or "right.parquet")
    staged_left.write_bytes(await left.read())
    staged_right.write_bytes(await right.read())
    try:
        return dataset.create_uploaded_dataset(staged_left, staged_right, dataset_id)
    except DatasetError as error:
        shutil.rmtree(DATA_ROOT / "datasets" / dataset_id, ignore_errors=True)
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)


@app.get("/api/datasets/{dataset_id}/keys")
def dataset_keys(dataset_id: str, q: str = "", limit: int = 200, sort: str = "hot") -> dict:
    try:
        return dataset.search_keys(dataset_id, query=q, limit=limit, sort=sort)
    except DatasetError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #


def _trial(request: PlanRequest) -> dict:
    left_path, right_path = dataset_paths(request.datasetId)
    return trial(
        left_path=left_path,
        right_path=right_path,
        partitions=request.partitions,
        salt=request.salt,
        hot_keys=request.hotKeys,
    )


@app.post("/api/plan")
def plan_trial(request: PlanRequest) -> dict:
    """Trial numbers only — nothing is written to disk."""
    try:
        return _trial(request)
    except DatasetError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PlanError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/plan/compare")
def plan_compare(request: CompareRequest) -> dict:
    """Side-by-side trials for several salt counts (and the unsalted baseline)."""
    try:
        results = []
        baseline = trial(
            left_path=dataset_paths(request.datasetId)[0],
            right_path=dataset_paths(request.datasetId)[1],
            partitions=request.partitions,
            salt=MIN_SALT,
            hot_keys=[],
        )
        results.append({"salt": 0, "label": "不拆（普通方案）", "trial": baseline})
        for salt in sorted({max(MIN_SALT, min(MAX_SALT, s)) for s in request.salts}):
            result = trial(
                left_path=dataset_paths(request.datasetId)[0],
                right_path=dataset_paths(request.datasetId)[1],
                partitions=request.partitions,
                salt=salt,
                hot_keys=request.hotKeys,
            )
            results.append({"salt": salt, "label": f"{salt} 个盐桶", "trial": result})
        return {"comparisons": results}
    except DatasetError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PlanError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #


@app.post("/api/execute")
def execute_plan(request: PlanRequest) -> dict:
    """Write the physical partitions, join bucket by bucket and verify."""
    validate_parameters(request.partitions, request.salt)
    try:
        left_path, right_path = dataset_paths(request.datasetId)
    except DatasetError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    con = duckdb.connect()
    try:
        left_counts = dataset._key_counts(con, left_path)
        right_counts = dataset._key_counts(con, right_path)
        trial_result = trial(
            left_path=left_path,
            right_path=right_path,
            partitions=request.partitions,
            salt=request.salt,
            hot_keys=request.hotKeys,
        )
    except PlanError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        con.close()

    plan = build_plan(
        partitions=request.partitions,
        salt=request.salt,
        hot_keys=request.hotKeys,
        left_counts=left_counts,
        right_counts=right_counts,
        require_hot=False,
    )
    job_id = uuid.uuid4().hex[:12]
    work_dir = JOBS_ROOT / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        outcome = engine.execute(plan=plan, left_path=left_path, right_path=right_path, work_dir=work_dir)
    except Exception as error:  # noqa: BLE001 - surface execution failure with context
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"execution failed: {error}") from error

    job = {
        "id": job_id,
        "datasetId": request.datasetId,
        "createdAt": int(started * 1000),
        "elapsedSeconds": round(time.time() - started, 4),
        "request": request.model_dump(),
        "plan": trial_result,
        "files": [file.to_json() for file in outcome.files],
        "batches": outcome.batches,
        "result": {
            "rows": outcome.result_rows,
            "path": str(work_dir / "result.parquet"),
            "resultParquetBytes": outcome.result_parquet_bytes,
        },
        "verification": outcome.verification,
        "storage": {
            "bucketParquetBytes": outcome.storage_bytes,
            "sourceParquetBytes": left_path.stat().st_size + right_path.stat().st_size,
        },
        "timing": {"writeSeconds": outcome.write_seconds, "joinSeconds": outcome.join_seconds},
    }
    engine.write_json(work_dir / "job.json", job)
    with _jobs_lock:
        _jobs[job_id] = job
    return job


@app.get("/api/jobs")
def list_jobs() -> dict:
    with _jobs_lock:
        items = [
            {
                "id": job["id"],
                "datasetId": job["datasetId"],
                "createdAt": job["createdAt"],
                "rows": job["result"]["rows"],
                "passed": job["verification"]["passed"],
                "hotKeys": [item["key"] for item in job["plan"]["hotKeys"]],
                "salt": job["plan"]["salt"],
                "partitions": job["plan"]["partitions"],
            }
            for job in _jobs.values()
        ]
    items.sort(key=lambda item: item["createdAt"], reverse=True)
    return {"jobs": items}


def _job(job_id: str) -> dict:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        job_path = JOBS_ROOT / job_id / "job.json"
        if job_path.is_file():
            job = engine.read_json(job_path)
            with _jobs_lock:
                _jobs[job_id] = job
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = _job(job_id)
    return engine.read_json(JOBS_ROOT / job_id / "job.json") | {
        "files": job["files"],
        "batches": job["batches"],
        "verification": job["verification"],
    }


@app.get("/api/jobs/{job_id}/keys/{key:path}")
def job_key_detail(job_id: str, key: str) -> dict:
    """Bucket placement, replication and before/after accounting for one key."""
    job = _job(job_id)
    left_path, right_path = dataset_paths(job["datasetId"])
    return keys.key_breakdown(
        work_dir=JOBS_ROOT / job_id,
        left_path=left_path,
        right_path=right_path,
        key=key,
    )


@app.get("/api/jobs/{job_id}/result")
def job_result(job_id: str, limit: int = 20) -> dict:
    """Sample join output, ordered by pair identity, plus the total."""
    job = _job(job_id)
    path = JOBS_ROOT / job_id / "result.parquet"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="result file is gone")
    limit = max(1, min(limit, 500))
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"SELECT left_row_id, right_row_id, join_key, left_payload, right_payload "
            f"FROM read_parquet({sql_string(path)}) ORDER BY left_row_id, right_row_id LIMIT {limit}"
        ).fetchall()
        total = int(con.execute(f"SELECT count(*) FROM read_parquet({sql_string(path)})").fetchone()[0])
    finally:
        con.close()
    return {
        "rows": [
            {
                "leftRowId": int(left_row_id),
                "rightRowId": int(right_row_id),
                "joinKey": join_key,
                "leftPayload": left_payload,
                "rightPayload": right_payload,
            }
            for left_row_id, right_row_id, join_key, left_payload, right_payload in rows
        ],
        "total": total,
    }


@app.get("/api/jobs/{job_id}/files")
def job_files(job_id: str) -> dict:
    job = _job(job_id)
    return {
        "files": job["files"],
        "storage": job["storage"],
        "batches": job["batches"],
        "plan": job["plan"],
        "verification": job["verification"],
        "timing": job["timing"],
    }


@app.get("/api/jobs/{job_id}/baseline")
def job_baseline(job_id: str, limit: int = 20) -> dict:
    """Direct DuckDB equi-inner join on the two source files."""
    job = _job(job_id)
    left_path, right_path = dataset_paths(job["datasetId"])
    limit = max(1, min(limit, 500))
    con = duckdb.connect()
    started = time.perf_counter()
    try:
        total = int(
            con.execute(
                f"SELECT count(*) FROM read_parquet({sql_string(left_path)}) l "
                f"INNER JOIN read_parquet({sql_string(right_path)}) r ON l.join_key = r.join_key "
                f"WHERE l.join_key IS NOT NULL AND l.join_key <> '' "
                f"AND r.join_key IS NOT NULL AND r.join_key <> ''"
            ).fetchone()[0]
        )
        seconds = time.perf_counter() - started
        rows = con.execute(
            f"SELECT l.row_id, r.row_id, l.join_key, l.payload, r.payload "
            f"FROM read_parquet({sql_string(left_path)}) l "
            f"INNER JOIN read_parquet({sql_string(right_path)}) r ON l.join_key = r.join_key "
            f"WHERE l.join_key IS NOT NULL AND l.join_key <> '' "
            f"AND r.join_key IS NOT NULL AND r.join_key <> '' "
            f"ORDER BY l.row_id, r.row_id LIMIT {limit}"
        ).fetchall()
    finally:
        con.close()
    return {
        "rows": total,
        "seconds": round(seconds, 5),
        "sample": [
            {
                "leftRowId": int(left_row_id),
                "rightRowId": int(right_row_id),
                "joinKey": key,
                "leftPayload": left_payload,
                "rightPayload": right_payload,
            }
            for left_row_id, right_row_id, key, left_payload, right_payload in rows
        ],
    }
