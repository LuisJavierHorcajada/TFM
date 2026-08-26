import csv
import io
import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response

from app.database import database
from app.services.registry import registry

router = APIRouter()


def _flatten_doc(d: dict[str, Any], parent_key: str = "", sep: str = ".") -> dict[str, Any]:
    """Recursively flatten nested dictionary for CSV serialization."""
    items: list[tuple[str, Any]] = []
    for k, v in d.items():
        if k == "_id":
            continue
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(_flatten_doc(v, new_key, sep=sep).items())
        elif isinstance(v, list):
            items.append((new_key, ", ".join(str(x) for x in v)))
        elif isinstance(v, datetime):
            items.append((new_key, v.isoformat()))
        else:
            items.append((new_key, v))
    return dict(items)


def _export_to_csv(docs: list[dict[str, Any]]) -> str:
    """Convert list of MongoDB benchmark result documents to CSV."""
    if not docs:
        return ""

    flat_docs = [_flatten_doc(doc) for doc in docs]

    priority_order = [
        "run_id",
        "timestamp",
        "status",
        "duration_s",
        "progress",
        "system_info.platform.provider",
        "system_info.platform.instance_type",
        "system_info.platform.region",
        "system_info.hostname",
        "system_info.os",
        "system_info.os_version",
        "system_info.cpu_model",
        "system_info.cpu_count",
        "system_info.ram_total_gb",
        "system_info.ram_available_gb",
        "system_info.disk_total_gb",
        "system_info.disk_available_gb",
        "system_info.python_version",
        "benchmarks_requested",
    ]

    all_keys: set[str] = set()
    for fd in flat_docs:
        all_keys.update(fd.keys())

    fieldnames = [k for k in priority_order if k in all_keys]
    remaining_keys = sorted(k for k in all_keys if k not in priority_order)
    fieldnames.extend(remaining_keys)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for fd in flat_docs:
        writer.writerow(fd)

    return output.getvalue()


@router.get("/export")
async def export_all_results(
    format: Literal["json", "csv"] = Query("json", description="Export format (json or csv)"),
    status: str | None = None,
    category: str | None = None,
):
    """Export multiple benchmark results as JSON or CSV."""
    collection = database.get_collection("results")

    query_filter: dict = {}
    if status:
        query_filter["status"] = status
    if category:
        matched_benchmarks = [
            name
            for name, bm in registry.benchmarks.items()
            if getattr(bm.info, "category", None) == category or name.startswith(category)
        ]
        if matched_benchmarks:
            query_filter["$or"] = [
                {f"results.{name}": {"$exists": True}} for name in matched_benchmarks
            ]
        else:
            query_filter[f"results.{category}_benchmark"] = {"$exists": True}

    cursor = collection.find(query_filter, {"_id": 0}).sort("timestamp", -1)
    results = await cursor.to_list(length=1000)

    if format == "csv":
        csv_content = _export_to_csv(results)
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="benchmark_results.csv"',
                "Content-Type": "text/csv; charset=utf-8",
            },
        )

    json_content = json.dumps(
        results,
        indent=2,
        default=lambda o: o.isoformat() if isinstance(o, datetime) else str(o),
    )
    return Response(
        content=json_content,
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="benchmark_results.json"',
            "Content-Type": "application/json; charset=utf-8",
        },
    )


@router.get("")
async def list_results(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    category: str | None = None,
    status: str | None = None,
):
    collection = database.get_collection("results")

    query_filter: dict = {}
    if status:
        query_filter["status"] = status
    if category:
        # Match benchmark names belonging to this category from registry
        matched_benchmarks = [
            name
            for name, bm in registry.benchmarks.items()
            if getattr(bm.info, "category", None) == category or name.startswith(category)
        ]
        if matched_benchmarks:
            query_filter["$or"] = [
                {f"results.{name}": {"$exists": True}} for name in matched_benchmarks
            ]
        else:
            query_filter[f"results.{category}_benchmark"] = {"$exists": True}

    # Count total
    total = await collection.count_documents(query_filter)

    # Get results for history page (newest first).
    cursor = (
        collection.find(query_filter, {"_id": 0})
        .sort("timestamp", -1)
        .skip((page - 1) * per_page)
        .limit(per_page)
    )
    results = await cursor.to_list(length=per_page)

    return {
        "results": results,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
    }


@router.get("/compare")
@router.post("/compare")
async def compare_results(run_id_a: str, run_id_b: str):
    """Compare two benchmark runs."""
    collection = database.get_collection("results")

    result_a = await collection.find_one({"run_id": run_id_a}, {"_id": 0})
    result_b = await collection.find_one({"run_id": run_id_b}, {"_id": 0})

    if not result_a:
        raise HTTPException(status_code=404, detail=f"Run '{run_id_a}' not found")
    if not result_b:
        raise HTTPException(status_code=404, detail=f"Run '{run_id_b}' not found")

    return {
        "run_a": result_a,
        "run_b": result_b,
    }


@router.get("/{run_id}/export")
async def export_single_result(
    run_id: str,
    format: Literal["json", "csv"] = Query("json", description="Export format (json or csv)"),
):
    """Export a single benchmark run as JSON or CSV."""
    collection = database.get_collection("results")
    result = await collection.find_one({"run_id": run_id}, {"_id": 0})

    if not result:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    short_id = run_id[:8]

    if format == "csv":
        csv_content = _export_to_csv([result])
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="benchmark_{short_id}.csv"',
                "Content-Type": "text/csv; charset=utf-8",
            },
        )

    json_content = json.dumps(
        result,
        indent=2,
        default=lambda o: o.isoformat() if isinstance(o, datetime) else str(o),
    )
    return Response(
        content=json_content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="benchmark_{short_id}.json"',
            "Content-Type": "application/json; charset=utf-8",
        },
    )


@router.get("/{run_id}")
async def get_result(run_id: str):
    """Get a single result by run_id."""
    collection = database.get_collection("results")
    result = await collection.find_one({"run_id": run_id}, {"_id": 0})

    if not result:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    return result


@router.delete("/{run_id}")
async def delete_result(run_id: str):
    """Delete a benchmark result."""
    collection = database.get_collection("results")
    result = await collection.delete_one({"run_id": run_id})

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    return {"message": f"Run '{run_id}' deleted", "deleted": True}
