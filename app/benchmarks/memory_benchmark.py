"""
Memory Benchmark Plugin.

Tests:
  - Sequential write bandwidth
  - Random access latency
  - Allocation stress
  - System memory info
"""

import asyncio
import random
import time

import psutil

from app.services.base import Benchmark, BenchmarkInfo

# --- Benchmark Configuration ---
SIZE_MB = 128
NUM_ELEMENTS = 2000000
NUM_READS = 500000
ALLOC_ITERATIONS = 100000
ALLOC_BLOCK_SIZE = 1024
# -------------------------------


def _sequential_bandwidth(size_mb: int) -> float:
    """Write sequentially to a bytearray. Returns MB/s."""
    size = size_mb * 1024 * 1024
    buf = bytearray(size)
    pattern = b"\xAA" * 4096

    start = time.perf_counter()
    offset = 0
    while offset < size:
        end = min(offset + 4096, size)
        buf[offset:end] = pattern[: end - offset]
        offset = end
    elapsed = time.perf_counter() - start

    return round(size_mb / max(elapsed, 0.0001), 2)


def _random_access_latency(num_elements: int, num_reads: int) -> float:
    """Random reads from a large list. Returns average latency in nanoseconds."""
    data = list(range(num_elements))
    random.seed(42)
    indices = []
    for i in range(num_reads):
        indices.append(random.randint(0, num_elements - 1))

    start = time.perf_counter()
    total = 0
    for idx in indices:
        total += data[idx]
    elapsed = time.perf_counter() - start

    latency_ns = (elapsed / num_reads) * 1e9
    return round(latency_ns, 2)


def _allocation_stress(iterations: int) -> float:
    """Rapidly allocate and free memory blocks. Returns elapsed seconds."""
    start = time.perf_counter()
    for i in range(iterations):
        block = bytearray(ALLOC_BLOCK_SIZE)
        del block
    elapsed = time.perf_counter() - start
    return round(elapsed, 4)


class MemoryBenchmark(Benchmark):
    """Memory bandwidth, latency, and allocation benchmark."""

    @property
    def info(self) -> BenchmarkInfo:
        return BenchmarkInfo(
            name="memory_benchmark",
            display_name="Memory Benchmark",
            description="Tests memory sequential bandwidth, random access latency, and allocation throughput.",
            category="memory",
        )

    async def run(self, params: dict | None = None) -> dict:
        p = params or {}
        size_mb = p.get("size_mb", SIZE_MB)
        num_elements = p.get("num_elements", NUM_ELEMENTS)
        num_reads = p.get("num_reads", NUM_READS)
        alloc_iterations = p.get("alloc_iterations", ALLOC_ITERATIONS)

        loop = asyncio.get_event_loop()

        # 1. Sequential bandwidth
        bandwidth = await loop.run_in_executor(None, _sequential_bandwidth, size_mb)

        # 2. Random access latency
        latency = await loop.run_in_executor(
            None, _random_access_latency, num_elements, num_reads
        )

        # 3. Allocation stress
        alloc_time = await loop.run_in_executor(
            None, _allocation_stress, alloc_iterations
        )

        # 4. System memory info
        mem = psutil.virtual_memory()

        return {
            "sequential_bandwidth": {
                "size_mb": size_mb,
                "bandwidth_mb_s": bandwidth,
            },
            "random_access": {
                "num_elements": num_elements,
                "num_reads": num_reads,
                "latency_ns": latency,
            },
            "allocation_stress": {
                "iterations": alloc_iterations,
                "time_s": alloc_time,
            },
            "system_memory": {
                "total_gb": round(mem.total / (1024**3), 2),
                "available_gb": round(mem.available / (1024**3), 2),
                "used_percent": mem.percent,
            },
        }
