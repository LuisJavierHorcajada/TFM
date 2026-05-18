"""
ESI-Bench - Runner / Orchestrator.

Manages the full lifecycle of a benchmark run:
    1. Collects system info
    2. Run each benchmark that the user selected.
    3. Check the progress of the benchmarks.
    4. Save the results to MongoDB.
"""

import platform
import time
import traceback
import uuid
from datetime import datetime, timezone

import psutil

from app.database import database
from app.models.schemas import BenchmarkResultDoc, RunRequest, SystemInfo
from app.services.registry import registry

# Current active runs
_active_runs: dict[str, BenchmarkResultDoc] = {}


def _collect_system_info() -> SystemInfo:
    """Gather platform metadata."""
    mem = psutil.virtual_memory()

    # Try to get CPU model name
    cpu_model = platform.processor() or "Unknown"
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.strip().startswith("model name"):
                    cpu_model = line.split(":")[1].strip()
                    break
    except (FileNotFoundError, PermissionError):
        pass

    return SystemInfo(
        hostname=platform.node(),
        os=platform.system(),
        os_version=platform.release(),
        cpu_model=cpu_model,
        cpu_count=psutil.cpu_count(logical=True) or 1,
        ram_total_gb=round(mem.total / (1024**3), 2),
        ram_available_gb=round(mem.available / (1024**3), 2),
        python_version=platform.python_version(),
    )


async def start_run(request: RunRequest) -> str:
    """
    Create a new run, store it in-memory, and return the run_id.
    The actual execution happens in execute_run() as a background task.
    """
    run_id = str(uuid.uuid4())

    # Resolve benchmark names
    if "all" in request.benchmarks:
        benchmark_names = registry.get_all_names()
    else:
        benchmark_names = []
        for name in request.benchmarks:
            if registry.get_benchmark(name):
                benchmark_names.append(name)

    doc = BenchmarkResultDoc(
        run_id=run_id,
        timestamp=datetime.now(timezone.utc),
        status="pending",
        benchmarks_requested=benchmark_names,
    )

    _active_runs[run_id] = doc

    # Also persist the initial record to MongoDB
    collection = database.get_collection("results")
    await collection.insert_one(doc.model_dump())

    return run_id


async def execute_run(run_id: str, params: dict | None = None) -> None:
    """
    Execute all benchmarks for a given run.
    Called as a FastAPI BackgroundTask.
    """
    doc = _active_runs.get(run_id)
    if not doc:
        return

    collection = database.get_collection("results")
    total = len(doc.benchmarks_requested)

    # Update status to running
    doc.status = "running"
    doc.system_info = _collect_system_info()
    await collection.update_one(
        {"run_id": run_id},
        {"$set": {"status": "running", "system_info": doc.system_info.model_dump()}},
    )

    overall_start = time.perf_counter()

    for idx, benchmark_name in enumerate(doc.benchmarks_requested, 1):
        benchmark = registry.get_benchmark(benchmark_name)
        if not benchmark:
            doc.results[benchmark_name] = {"error": "Benchmark not found"}
            continue

        doc.current_benchmark = benchmark.info.display_name
        doc.progress = f"{idx}/{total}"

        await collection.update_one(
            {"run_id": run_id},
            {
                "$set": {
                    "current_benchmark": doc.current_benchmark,
                    "progress": doc.progress,
                }
            },
        )

        try:
            result = await benchmark.run(params)
            doc.results[benchmark_name] = result
        except Exception as e:
            doc.results[benchmark_name] = {
                "error": str(e),
                "traceback": traceback.format_exc(),
            }

        # Persist intermediate results
        await collection.update_one(
            {"run_id": run_id},
            {"$set": {"results": doc.results}},
        )

    # Finalise
    doc.duration_s = round(time.perf_counter() - overall_start, 3)
    doc.status = "completed"
    doc.current_benchmark = ""
    doc.progress = f"{total}/{total}"

    await collection.update_one(
        {"run_id": run_id},
        {
            "$set": {
                "status": "completed",
                "duration_s": doc.duration_s,
                "results": doc.results,
                "current_benchmark": "",
                "progress": doc.progress,
            }
        },
    )

    # Clean up in-memory tracker
    _active_runs.pop(run_id, None)


def get_run_status(run_id: str) -> BenchmarkResultDoc | None:
    """Get the in-memory status of an active run."""
    return _active_runs.get(run_id)
