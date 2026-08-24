"""
CPU Benchmark Plugin.

Tests:
  - Prime sieve (Eratosthenes) - Measures integer performance
  - Matrix multiplication - Measures floating point performance
  - zlib compression - Measures mixed CPU workload
  - Multi-core prime sieve - Measures parallel performance
"""

import asyncio
import concurrent.futures
import math
import os
import random
import time
import zlib

from app.services.base import Benchmark, BenchmarkInfo

# --- Benchmark Configuration ---
PRIME_LIMIT = 100_000_000  # 100M (uses ~100MB RAM, safe for 1GB VMs)
MATRIX_SIZE = 350          # 350x350 pure Python floating point multiplication
COMPRESSION_MB = 64        # 64MB zlib compression
COMPRESSION_LEVEL = 6

# Baseline reference times (in seconds) for score normalization (1000 = baseline)
REF_PRIME_TIME = 5.0
REF_MATRIX_TIME = 4.0
REF_COMPRESSION_TIME = 2.0
# -------------------------------


def _sieve_of_eratosthenes(limit: int) -> int:
    """Count primes up to `limit` using Sieve of Eratosthenes."""
    if limit < 2:
        return 0
    sieve = bytearray([1]) * (limit + 1)
    sieve[0] = sieve[1] = 0
    for i in range(2, int(math.isqrt(limit)) + 1):
        if sieve[i]:
            for j in range(i * i, limit + 1, i):
                sieve[j] = 0
    return sum(sieve)


def _matrix_multiply(size: int) -> float:
    """NxN matrix multiplication. Returns execution time in seconds."""
    random.seed(42)
    A = [[random.random() for _ in range(size)] for _ in range(size)]
    B = [[random.random() for _ in range(size)] for _ in range(size)]

    start = time.perf_counter()

    C = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(size):
            s = 0.0
            for k in range(size):
                s += A[i][k] * B[k][j]
            C[i][j] = s

    return time.perf_counter() - start


def _compression_benchmark(data_size_mb: int) -> float:
    """Compress a buffer with zlib. Returns execution time in seconds."""
    data = os.urandom(data_size_mb * 1024 * 1024)
    start = time.perf_counter()
    zlib.compress(data, level=COMPRESSION_LEVEL)
    return time.perf_counter() - start


def _worker_prime(limit: int) -> int:
    """Worker function for multi-core prime sieve."""
    return _sieve_of_eratosthenes(limit)


class CPUBenchmark(Benchmark):
    """CPU integer, floating-point, compression, and multi-core benchmark."""

    @property
    def info(self) -> BenchmarkInfo:
        return BenchmarkInfo(
            name="cpu_benchmark",
            display_name="CPU Benchmark",
            description="Tests CPU integer throughput, floating-point performance, compression, and multi-core scaling.",
            category="cpu",
        )

    async def run(self, params: dict | None = None) -> dict:
        p = params or {}
        prime_limit = p.get("prime_limit", PRIME_LIMIT)
        matrix_size = p.get("matrix_size", MATRIX_SIZE)
        compression_mb = p.get("compression_mb", COMPRESSION_MB)

        loop = asyncio.get_event_loop()

        # 1. Single-core prime sieve
        t0 = time.perf_counter()
        prime_count = await loop.run_in_executor(
            None, _sieve_of_eratosthenes, prime_limit
        )
        prime_time = time.perf_counter() - t0

        # 2. Matrix multiplication
        matrix_time = await loop.run_in_executor(None, _matrix_multiply, matrix_size)

        # 3. Compression
        compression_time = await loop.run_in_executor(
            None, _compression_benchmark, compression_mb
        )

        # 4. Multi-core prime sieve
        cpu_count = os.cpu_count() or 1
        t0 = time.perf_counter()
        with concurrent.futures.ProcessPoolExecutor(max_workers=cpu_count) as executor:
            futures = [executor.submit(_worker_prime, prime_limit) for _ in range(cpu_count)]
            concurrent.futures.wait(futures)
        multi_core_time = time.perf_counter() - t0

        # Reference-normalized scoring (1000 = baseline, higher is better)
        single_core_score = round(
            (
                (REF_PRIME_TIME / max(prime_time, 0.001)) * 0.5
                + (REF_MATRIX_TIME / max(matrix_time, 0.001)) * 0.3
                + (REF_COMPRESSION_TIME / max(compression_time, 0.001)) * 0.2
            )
            * 1000,
            2,
        )

        multi_core_score = round(
            ((REF_PRIME_TIME * cpu_count) / max(multi_core_time, 0.001)) * 1000,
            2,
        )

        return {
            "prime_sieve": {
                "limit": prime_limit,
                "primes_found": prime_count,
                "time_s": round(prime_time, 4),
            },
            "matrix_multiply": {
                "size": f"{matrix_size}x{matrix_size}",
                "time_s": round(matrix_time, 4),
            },
            "compression": {
                "data_size_mb": compression_mb,
                "time_s": round(compression_time, 4),
            },
            "multi_core": {
                "workers": cpu_count,
                "time_s": round(multi_core_time, 4),
            },
            "scores": {
                "single_core": single_core_score,
                "multi_core": multi_core_score,
            },
        }
