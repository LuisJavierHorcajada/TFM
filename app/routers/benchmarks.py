"""
ESI-Bench - Benchmark API routes.
"""

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.models.schemas import RunRequest, RunResponse, RunStatus
from app.services.registry import registry
from app.services.runner import execute_run, get_run_status, start_run
from app.database import database

router = APIRouter()


@router.get("")
async def list_benchmarks():
    """List all registered benchmarks."""
    return {
        "benchmarks": registry.list_benchmarks(),
        "total": len(registry.benchmarks),
    }




@router.post("/run", response_model=RunResponse)
async def run_benchmarks(request: RunRequest, background_tasks: BackgroundTasks):
    """Start a benchmark run. Returns immediately with a run_id."""
    if "all" not in request.benchmarks:
        available = registry.get_all_names()
        invalid = []
        for name in request.benchmarks:
            if name not in available:
                invalid.append(name)
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown benchmarks: {invalid}. Available: {available}",
            )

    run_id = await start_run(request)

    # Checks which benchmarks the user selected
    if "all" in request.benchmarks:
        benchmark_names = registry.get_all_names()
    else:
        benchmark_names = request.benchmarks

    # Schedule execution as a background task to have concurrency.
    background_tasks.add_task(execute_run, run_id, request.params)

    return RunResponse(
        run_id=run_id,
        status="pending",
        benchmarks=benchmark_names,
    )


@router.get("/status/{run_id}", response_model=RunStatus)
async def benchmark_status(run_id: str):
    """Get the status of a benchmark run."""
    # Check if the execution is currently running.
    doc = get_run_status(run_id)
    if doc:
        return RunStatus(
            run_id=doc.run_id,
            status=doc.status,
            progress=doc.progress,
            current_benchmark=doc.current_benchmark,
            error=doc.error,
        )

    # Check MongoDB for completed benchmarks.
    collection = database.get_collection("results")
    result = await collection.find_one({"run_id": run_id})
    if not result:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    return RunStatus(
        run_id=result["run_id"],
        status=result.get("status", "unknown"),
        progress=result.get("progress", ""),
        current_benchmark=result.get("current_benchmark", ""),
        error=result.get("error"),
    )
