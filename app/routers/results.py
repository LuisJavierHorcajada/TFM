"""
ESI-Bench - Results API routes.
"""

from fastapi import APIRouter, HTTPException, Query


from app.database import database

router = APIRouter()


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
        # Filter runs that include a specific benchmark category --> Doesn't do anything right now.
        query_filter[f"results.{category}"] = {"$exists": True}

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


@router.post("/compare")
async def compare_results(run_id_a: str, run_id_b: str):
    """Compare two benchmark runs"""
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
