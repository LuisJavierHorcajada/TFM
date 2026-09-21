"""
FIWARE Integration Service.

Provides helpers to:
  - Check Orion Context Broker reachability
  - Publish benchmark run results as NGSI-LD / NGSI-v2 entities to Orion
"""

import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger("esi_bench.fiware_service")

# Optional httpx import
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


async def is_orion_reachable(orion_url: str, timeout: float = 3.0) -> bool:
    """Check if Orion Context Broker is reachable at /version."""
    if not orion_url:
        return False
    url = f"{orion_url.rstrip('/')}/version"

    if HAS_HTTPX:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url)
                return resp.status_code == 200
        except Exception:
            return False

    def _sync_check():
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.status == 200
        except Exception:
            return False

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_check)


def _build_ngsi_ld_entity(run_id: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Convert an ESI-Bench result document to an NGSI-LD BenchmarkRun entity."""
    sys_info = doc.get("system_info") or {}
    platform_info = sys_info.get("platform") or {}
    results = doc.get("results") or {}

    # Extract high-level scores
    scores: dict[str, Any] = {}
    for bm_name, bm_data in results.items():
        if isinstance(bm_data, dict) and "scores" in bm_data:
            scores[bm_name] = bm_data["scores"]

    ts = doc.get("timestamp")
    ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts or "")

    entity = {
        "id": f"urn:ngsi-ld:BenchmarkRun:{run_id}",
        "type": "BenchmarkRun",
        "@context": [
            "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld"
        ],
        "profile": {
            "type": "Property",
            "value": doc.get("profile", "bare"),
        },
        "status": {
            "type": "Property",
            "value": doc.get("status", "completed"),
        },
        "durationSeconds": {
            "type": "Property",
            "value": doc.get("duration_s", 0.0),
        },
        "platform": {
            "type": "Property",
            "value": {
                "provider": platform_info.get("provider", "unknown"),
                "instanceType": platform_info.get("instance_type"),
                "region": platform_info.get("region"),
                "hostname": sys_info.get("hostname"),
                "cpuModel": sys_info.get("cpu_model"),
                "cpuCount": sys_info.get("cpu_count"),
                "ramTotalGB": sys_info.get("ram_total_gb"),
                "diskTotalGB": sys_info.get("disk_total_gb"),
            },
        },
        "scores": {
            "type": "Property",
            "value": scores,
        },
        "createdAt": {
            "type": "Property",
            "value": ts_str,
        },
    }
    return entity


async def publish_benchmark_result(
    orion_url: str, run_id: str, result_doc: dict[str, Any]
) -> bool:
    """
    Publishes the benchmark result to Orion as an NGSI-LD entity.
    Falls back gracefully if Orion is unreachable or rejects the payload.
    """
    if not orion_url:
        return False

    base_url = orion_url.rstrip("/")
    entity = _build_ngsi_ld_entity(run_id, result_doc)
    body_bytes = json.dumps(entity).encode("utf-8")

    # Try NGSI-LD endpoint first
    ngsi_ld_url = f"{base_url}/ngsi-ld/v1/entities"
    headers_ld = {
        "Content-Type": "application/ld+json",
    }

    try:
        if HAS_HTTPX:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(ngsi_ld_url, content=body_bytes, headers=headers_ld)
                if resp.status_code in (200, 201, 204):
                    logger.info("Published benchmark run %s to Orion (NGSI-LD)", run_id)
                    return True
                if resp.status_code == 409:
                    # Entity already exists: update attributes
                    update_url = f"{base_url}/ngsi-ld/v1/entities/{entity['id']}/attrs"
                    resp_patch = await client.patch(update_url, content=body_bytes, headers=headers_ld)
                    return resp_patch.status_code in (200, 204)
        else:
            def _post_sync():
                req = urllib.request.Request(ngsi_ld_url, data=body_bytes, headers=headers_ld, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=5.0) as response:
                        return response.status in (200, 201, 204)
                except urllib.error.HTTPError as e:
                    if e.code == 409:
                        update_url = f"{base_url}/ngsi-ld/v1/entities/{entity['id']}/attrs"
                        req_patch = urllib.request.Request(
                            update_url, data=body_bytes, headers=headers_ld, method="PATCH"
                        )
                        with urllib.request.urlopen(req_patch, timeout=5.0) as resp2:
                            return resp2.status in (200, 204)
                    return False

            loop = asyncio.get_event_loop()
            ok = await loop.run_in_executor(None, _post_sync)
            if ok:
                logger.info("Published benchmark run %s to Orion (NGSI-LD)", run_id)
                return True

    except Exception as e:
        logger.warning("NGSI-LD publish failed (%s). Attempting NGSI-v2 fallback...", e)

    # NGSI-v2 Fallback (for standard Orion without LD plugin)
    try:
        ngsi_v2_url = f"{base_url}/v2/entities?options=upsert"
        v2_entity = {
            "id": f"urn:ngsi-v2:BenchmarkRun:{run_id}",
            "type": "BenchmarkRun",
            "profile": {"value": result_doc.get("profile", "bare"), "type": "Text"},
            "status": {"value": result_doc.get("status", "completed"), "type": "Text"},
            "scores": {"value": json.dumps(entity["scores"]["value"]), "type": "StructuredValue"},
        }
        v2_body = json.dumps(v2_entity).encode("utf-8")
        headers_v2 = {"Content-Type": "application/json"}

        if HAS_HTTPX:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(ngsi_v2_url, content=v2_body, headers=headers_v2)
                if resp.status_code in (200, 201, 204):
                    logger.info("Published benchmark run %s to Orion (NGSI-v2)", run_id)
                    return True
        else:
            def _post_v2_sync():
                req = urllib.request.Request(ngsi_v2_url, data=v2_body, headers=headers_v2, method="POST")
                with urllib.request.urlopen(req, timeout=5.0) as response:
                    return response.status in (200, 201, 204)

            loop = asyncio.get_event_loop()
            if await loop.run_in_executor(None, _post_v2_sync):
                logger.info("Published benchmark run %s to Orion (NGSI-v2)", run_id)
                return True

    except Exception as e:
        logger.warning("Failed to publish benchmark run %s to Orion: %s", run_id, e)

    return False

