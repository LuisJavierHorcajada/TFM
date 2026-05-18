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
import time
import zlib

from app.services.base import Benchmark, BenchmarkInfo

# --- Benchmark Configuration ---
PRIME_LIMIT = 1000000000
MATRIX_SIZE = 600
COMPRESSION_MB = 256
COMPRESSION_LEVEL = 6
# -------------------------------



def _sieve_of_eratosthenes(limit: int) -> int:
    """Count primes up to a `limit` using Sieve of Eratosthenes algorithm."""
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
    """NxN matrix multiplication. Returns time in seconds."""
    import random

    random.seed(42)
    A = []
    for i in range(size):
        row = []
        for j in range(size):
            row.append(random.random())
        A.append(row)

    B = []
    for i in range(size):
        row = []
        for j in range(size):
            row.append(random.random())
        B.append(row)

    start = time.perf_counter()

    C = []
    for i in range(size):
        C.append([0.0] * size)
    for i in range(size):
        for j in range(size):
            s = 0.0
            for k in range(size):
                s += A[i][k] * B[k][j]
            C[i][j] = s
    return time.perf_counter() - start


def _compression_benchmark(data_size_mb: int) -> float:
    """Compress a buffer with zlib. Returns time in seconds."""
    data = os.urandom(data_size_mb * 1024 * 1024)
    start = time.perf_counter()
    zlib.compress(data, level=COMPRESSION_LEVEL)
    return time.perf_counter() - start


def _worker_prime(limit: int) -> int:
    """Worker for multi-core test."""
    return _sieve_of_eratosthenes(limit)


class CPUBenchmark(Benchmark):
    """CPU benchmark."""

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
            futures = []
            for i in range(cpu_count):
                futures.append(executor.submit(_worker_prime, prime_limit))
            concurrent.futures.wait(futures)
        multi_core_time = time.perf_counter() - t0

        # Compute scores (higher is better) --> Need to find a better way to assign scores that take into account the results of other benchmarks.
        single_core_score = round(prime_limit / max(prime_time, 0.001) / 10000, 2)
        multi_core_score = round(
            (prime_limit * cpu_count) / max(multi_core_time, 0.001) / 10000, 2
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
