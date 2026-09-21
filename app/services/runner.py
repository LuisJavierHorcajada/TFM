"""
ESI-Bench - Runner / Orchestrator.

Manages the full lifecycle of a benchmark run:
    1. Collects system info
    2. Run each benchmark that the user selected.
    3. Check the progress of the benchmarks.
    4. Save the results to MongoDB.
"""

import asyncio
import logging
import platform
import time
import traceback
import uuid
from datetime import datetime, timezone

import os
import psutil

from app.config import settings
from app.database import database
from app.models.schemas import BenchmarkResultDoc, RunRequest, SystemInfo
from app.services.platform_detector import detect_platform
from app.services.registry import registry

logger = logging.getLogger("esi_bench.runner")

# Current active runs
_active_runs: dict[str, BenchmarkResultDoc] = {}


def _get_os_info() -> tuple[str, str]:
    """
    Get human-readable OS distribution name (e.g. 'Ubuntu 24.04.4 LTS') and kernel version.
    Checks /host/etc/os-release (host-mounted in container), /etc/os-release, and platform module.
    """
    os_name = platform.system()
    kernel_version = platform.release()
    pretty_os = None

    # 1. Try host /etc/os-release if mounted in container
    for os_release_path in ["/host/etc/os-release", "/etc/os-release"]:
        try:
            if os.path.exists(os_release_path):
                with open(os_release_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("PRETTY_NAME="):
                            pretty_os = line.split("=", 1)[1].strip('"\'')
                            break
                        if line.startswith("NAME=") and not pretty_os:
                            pretty_os = line.split("=", 1)[1].strip('"\'')
                if pretty_os:
                    break
        except Exception:
            pass

    # 2. Try platform.freedesktop_os_release (Python 3.10+)
    if not pretty_os and hasattr(platform, "freedesktop_os_release"):
        try:
            os_rel = platform.freedesktop_os_release()
            pretty_os = os_rel.get("PRETTY_NAME") or os_rel.get("NAME")
        except Exception:
            pass

    display_os = pretty_os if pretty_os else f"{os_name} {kernel_version}"
    return display_os, kernel_version


def _collect_system_info() -> SystemInfo:
    """Gather platform metadata."""
    mem = psutil.virtual_memory()

    # Collect disk space reliably across candidate paths
    disk_total_gb = None
    disk_available_gb = None
    candidate_paths = [
        getattr(settings, "BENCHMARK_DISK_PATH", "/tmp/benchmark"),
        "/tmp/benchmark",
        "/var/tmp",
        "/tmp",
        "/",
        "/home",
    ]
    for p in candidate_paths:
        try:
            check_p = p if os.path.exists(p) else os.path.dirname(p)
            if os.path.exists(check_p):
                usage = psutil.disk_usage(check_p)
                if usage.total > 0:
                    disk_total_gb = round(usage.total / (1024**3), 2)
                    disk_available_gb = round(usage.free / (1024**3), 2)
                    break
        except Exception:
            continue

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

    # Detect OS distribution and kernel release
    display_os, kernel_version = _get_os_info()

    # Detect cloud platform
    platform_info = detect_platform()

    return SystemInfo(
        hostname=platform.node(),
        os=display_os,
        os_version=kernel_version,
        cpu_model=cpu_model,
        cpu_count=psutil.cpu_count(logical=True) or 1,
        ram_total_gb=round(mem.total / (1024**3), 2),
        ram_available_gb=round(mem.available / (1024**3), 2),
        disk_total_gb=disk_total_gb,
        disk_available_gb=disk_available_gb,
        python_version=platform.python_version(),
        platform=platform_info,
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

    profile = getattr(request, "profile", None) or getattr(settings, "RUN_PROFILE", "bare")
    doc = BenchmarkResultDoc(
        run_id=run_id,
        profile=profile,
        timestamp=datetime.now(timezone.utc),
        status="pending",
        benchmarks_requested=benchmark_names,
    )

    _active_runs[run_id] = doc

    # Also persist the initial record to MongoDB
    collection = database.get_collection("results")
    doc_data = doc.model_dump() if hasattr(doc, "model_dump") else doc.dict()
    await collection.insert_one(doc_data)

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
    sys_info_data = (
        doc.system_info.model_dump()
        if hasattr(doc.system_info, "model_dump")
        else doc.system_info.dict()
    )
    await collection.update_one(
        {"run_id": run_id},
        {"$set": {"status": "running", "system_info": sys_info_data}},
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
            logger.exception("Benchmark '%s' failed in run %s: %s", benchmark_name, run_id, e)
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

    # Optional FIWARE publishing if target Orion is configured
    orion_target = (params.get("orion_url") if params else None) or getattr(settings, "ORION_URL", "")
    if orion_target:
        try:
            from app.services.fiware import publish_benchmark_result
            final_doc = doc.model_dump() if hasattr(doc, "model_dump") else doc.dict()
            asyncio.create_task(publish_benchmark_result(orion_target, run_id, final_doc))
        except Exception as e:
            logger.warning("Could not initiate FIWARE publishing: %s", e)

    # Clean up in-memory tracker
    _active_runs.pop(run_id, None)


def get_run_status(run_id: str) -> BenchmarkResultDoc | None:
    """Get the in-memory status of an active run."""
    return _active_runs.get(run_id)
