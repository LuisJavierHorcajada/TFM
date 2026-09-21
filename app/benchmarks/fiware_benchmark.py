"""
FIWARE Context Broker (Orion) Benchmark Plugin.

Tests:
  - Orion Health Check & Version Discovery (/version)
  - Single Entity CRUD Latency (Create, Read, Update, Delete) via NGSI-v2
  - Batch Entity Creation Throughput (/v2/op/update)
  - Filtered Entity Query Latency (/v2/entities)
"""

import asyncio
import json
import logging
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from app.config import settings
from app.services.base import Benchmark, BenchmarkInfo

logger = logging.getLogger("esi_bench.fiware")

# Optional httpx import
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


class HTTPClientHelper:
    """Async HTTP helper with httpx if available, fallback to urllib."""

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self._httpx_client = None

    async def __aenter__(self):
        if HAS_HTTPX:
            self._httpx_client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._httpx_client:
            await self._httpx_client.aclose()

    async def request(
        self, method: str, url: str, json_data: dict | list | None = None, headers: dict | None = None
    ) -> tuple[int, dict | list | str, float]:
        """
        Executes HTTP request, returns (status_code, response_data, elapsed_ms).
        """
        hdrs = headers.copy() if headers else {}
        body_bytes = None
        if json_data is not None:
            hdrs["Content-Type"] = "application/json"
            body_bytes = json.dumps(json_data).encode("utf-8")

        start = time.perf_counter()
        if self._httpx_client:
            resp = await self._httpx_client.request(
                method, url, content=body_bytes, headers=hdrs
            )
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            try:
                data = resp.json()
            except Exception:
                data = resp.text
            return resp.status_code, data, elapsed_ms

        # Fallback to standard library urllib
        def _urllib_call():
            req = urllib.request.Request(url, data=body_bytes, headers=hdrs, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    status = response.status
                    raw = response.read().decode("utf-8", errors="replace")
                    try:
                        parsed = json.loads(raw)
                    except Exception:
                        parsed = raw
                    return status, parsed
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(raw)
                except Exception:
                    parsed = raw
                return e.code, parsed
            except Exception as e:
                raise e

        loop = asyncio.get_event_loop()
        status, data = await loop.run_in_executor(None, _urllib_call)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return status, data, elapsed_ms


class FIWAREBenchmark(Benchmark):
    """Orion Context Broker performance benchmark."""

    @property
    def info(self) -> BenchmarkInfo:
        return BenchmarkInfo(
            name="fiware_benchmark",
            display_name="FIWARE Benchmark",
            description="Tests Orion Context Broker latency, batch throughput, and entity operations.",
            category="fiware",
        )

    async def run(self, params: dict | None = None) -> dict:
        p = params or {}
        orion_url = (p.get("orion_url") or settings.ORION_URL or "http://localhost:1026").rstrip("/")
        num_entities = int(p.get("num_entities", 50))
        batch_size = int(p.get("batch_size", 25))

        test_run_id = uuid.uuid4().hex[:8]
        entity_type = f"BenchEntity_{test_run_id}"

        results: dict = {
            "orion_url": orion_url,
            "target_entity_type": entity_type,
        }

        async with HTTPClientHelper(timeout=10.0) as client:
            # 1. Health & Version Check
            try:
                status, ver_data, health_ms = await client.request("GET", f"{orion_url}/version")
                if status != 200:
                    raise RuntimeError(f"Orion /version returned HTTP {status}: {ver_data}")
                orion_info = ver_data.get("orion", {}) if isinstance(ver_data, dict) else {}
                results["health"] = {
                    "status": "ok",
                    "response_time_ms": round(health_ms, 2),
                    "version": orion_info.get("version", "unknown"),
                    "uptime": orion_info.get("uptime", "unknown"),
                }
            except Exception as e:
                raise RuntimeError(f"Failed to connect to Orion at {orion_url}: {e}") from e

            # 2. Single Entity Create, Read, Update, Delete Latency
            # Test over min(num_entities, 20) to keep latency test fast and precise
            crud_count = min(num_entities, 25)
            create_times: list[float] = []
            read_times: list[float] = []
            update_times: list[float] = []
            delete_times: list[float] = []

            for i in range(crud_count):
                entity_id = f"urn:ngsi-v2:{entity_type}:crud_{i}"

                # CREATE
                entity_payload = {
                    "id": entity_id,
                    "type": entity_type,
                    "temperature": {"value": round(20.0 + i * 0.1, 2), "type": "Number"},
                    "iteration": {"value": i, "type": "Integer"},
                }
                status, _, ms = await client.request("POST", f"{orion_url}/v2/entities", json_data=entity_payload)
                if status in (201, 200):
                    create_times.append(ms)

                # READ
                status, _, ms = await client.request("GET", f"{orion_url}/v2/entities/{entity_id}")
                if status == 200:
                    read_times.append(ms)

                # UPDATE
                update_payload = {"temperature": {"value": round(30.0 + i * 0.1, 2), "type": "Number"}}
                status, _, ms = await client.request(
                    "PATCH", f"{orion_url}/v2/entities/{entity_id}/attrs", json_data=update_payload
                )
                if status in (200, 204):
                    update_times.append(ms)

                # DELETE
                status, _, ms = await client.request("DELETE", f"{orion_url}/v2/entities/{entity_id}")
                if status in (200, 204):
                    delete_times.append(ms)

            results["entity_create"] = {
                "avg_ms": round(statistics.mean(create_times), 2) if create_times else 0.0,
                "min_ms": round(min(create_times), 2) if create_times else 0.0,
                "max_ms": round(max(create_times), 2) if create_times else 0.0,
                "count": len(create_times),
            }
            results["entity_read"] = {
                "avg_ms": round(statistics.mean(read_times), 2) if read_times else 0.0,
                "min_ms": round(min(read_times), 2) if read_times else 0.0,
                "max_ms": round(max(read_times), 2) if read_times else 0.0,
                "count": len(read_times),
            }
            results["entity_update"] = {
                "avg_ms": round(statistics.mean(update_times), 2) if update_times else 0.0,
                "min_ms": round(min(update_times), 2) if update_times else 0.0,
                "max_ms": round(max(update_times), 2) if update_times else 0.0,
                "count": len(update_times),
            }
            results["entity_delete"] = {
                "avg_ms": round(statistics.mean(delete_times), 2) if delete_times else 0.0,
                "min_ms": round(min(delete_times), 2) if delete_times else 0.0,
                "max_ms": round(max(delete_times), 2) if delete_times else 0.0,
                "count": len(delete_times),
            }

            # 3. Batch Entity Creation Throughput (/v2/op/update)
            total_batch_entities = num_entities
            batch_entities: list[dict] = []
            for i in range(total_batch_entities):
                batch_entities.append(
                    {
                        "id": f"urn:ngsi-v2:{entity_type}:batch_{i}",
                        "type": entity_type,
                        "sensor_val": {"value": i * 1.5, "type": "Number"},
                        "tag": {"value": f"node_{i % 5}", "type": "Text"},
                    }
                )

            batch_start = time.perf_counter()
            created_batch_count = 0
            for offset in range(0, total_batch_entities, batch_size):
                chunk = batch_entities[offset : offset + batch_size]
                op_payload = {
                    "actionType": "append",
                    "entities": chunk,
                }
                status, _, _ = await client.request("POST", f"{orion_url}/v2/op/update", json_data=op_payload)
                if status in (200, 201, 204):
                    created_batch_count += len(chunk)

            batch_wall_time = time.perf_counter() - batch_start
            batch_throughput = round(created_batch_count / max(batch_wall_time, 0.001), 2)

            results["batch_create"] = {
                "total_entities": created_batch_count,
                "total_time_s": round(batch_wall_time, 4),
                "throughput_entities_per_s": batch_throughput,
            }

            # 4. Filtered Query Latency
            query_times: list[float] = []
            for query_limit in [10, 25, 50]:
                status, data, ms = await client.request(
                    "GET", f"{orion_url}/v2/entities?type={entity_type}&limit={query_limit}"
                )
                if status == 200:
                    query_times.append(ms)

            results["query"] = {
                "avg_ms": round(statistics.mean(query_times), 2) if query_times else 0.0,
                "min_ms": round(min(query_times), 2) if query_times else 0.0,
                "max_ms": round(max(query_times), 2) if query_times else 0.0,
            }

            # 5. Clean Up Batch Entities
            cleanup_payload = {
                "actionType": "delete",
                "entities": [{"id": e["id"], "type": entity_type} for e in batch_entities[:created_batch_count]],
            }
            try:
                await client.request("POST", f"{orion_url}/v2/op/update", json_data=cleanup_payload)
            except Exception as e:
                logger.warning("Cleanup error: %s", e)

        return results
