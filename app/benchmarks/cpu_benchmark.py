"""
CPU Benchmark Plugin.

Tests:
  - Single-Core: Integer (Primes), Floating-point (Matrix), and Compression workloads
  - Multi-Core: Parallel execution of the composite compute suite across all available cores
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
PRIME_LIMIT = 50_000_000   # 50M primes (~50MB RAM per worker, highly safe for 1GB VMs)
MATRIX_SIZE = 300          # 300x300 floating-point matrix multiplication
COMPRESSION_MB = 32        # 32MB zlib compression
COMPRESSION_LEVEL = 6

# Baseline reference times (in seconds) on a laptop running an Intel i7-8750H)
REF_PRIME_TIME = 6.5533
REF_MATRIX_TIME = 2.0503
REF_COMPRESSION_TIME = 0.9586
# -------------------------------


def _sieve_of_eratosthenes(limit: int) -> int:
    """Count primes up to `limit` using Sieve of Eratosthenes (Integer ALU test)."""
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
    """NxN floating-point matrix multiplication (FPU test). Returns execution time in seconds."""
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
    """Compress a buffer with zlib (Mixed compute/instruction cache test). Returns time in seconds."""
    data = os.urandom(data_size_mb * 1024 * 1024)
    start = time.perf_counter()
    zlib.compress(data, level=COMPRESSION_LEVEL)
    return time.perf_counter() - start


def _worker_composite_suite(args: tuple[int, int, int]) -> tuple[float, float, float]:
    """
    Worker task executing the composite compute suite for multi-core testing:
    1. Integer prime sieve
    2. Floating-point matrix multiply
    3. Zlib compression
    Returns individual elapsed times (t_prime, t_matrix, t_comp).
    """
    prime_limit, matrix_size, compression_mb = args

    # 1. Primes
    t0 = time.perf_counter()
    _sieve_of_eratosthenes(prime_limit)
    t_prime = time.perf_counter() - t0

    # 2. Matrix
    t_matrix = _matrix_multiply(matrix_size)

    # 3. Compression
    t_comp = _compression_benchmark(compression_mb)

    return (t_prime, t_matrix, t_comp)


class CPUBenchmark(Benchmark):
    """CPU integer, floating-point, compression, and multi-core scaling benchmark."""

    @property
    def info(self) -> BenchmarkInfo:
        return BenchmarkInfo(
            name="cpu_benchmark",
            display_name="CPU Benchmark",
            description="Tests single-core and multi-core integer throughput, floating-point math, and compression.",
            category="cpu",
        )

    async def run(self, params: dict | None = None) -> dict:
        p = params or {}
        prime_limit = p.get("prime_limit", PRIME_LIMIT)
        matrix_size = p.get("matrix_size", MATRIX_SIZE)
        compression_mb = p.get("compression_mb", COMPRESSION_MB)
        cpu_count = os.cpu_count() or 1

        loop = asyncio.get_event_loop()

        # -------------------------------------------------------------
        # 1. Single-Core Benchmark (Sequential execution of the suite)
        # -------------------------------------------------------------
        t0 = time.perf_counter()
        prime_count = await loop.run_in_executor(None, _sieve_of_eratosthenes, prime_limit)
        sc_prime_time = time.perf_counter() - t0

        sc_matrix_time = await loop.run_in_executor(None, _matrix_multiply, matrix_size)
        sc_comp_time = await loop.run_in_executor(None, _compression_benchmark, compression_mb)
        single_core_total = sc_prime_time + sc_matrix_time + sc_comp_time

        # Single-Core Score (1000 = baseline reference)
        single_core_score = round(
            (
                (REF_PRIME_TIME / max(sc_prime_time, 0.001)) * 0.45
                + (REF_MATRIX_TIME / max(sc_matrix_time, 0.001)) * 0.35
                + (REF_COMPRESSION_TIME / max(sc_comp_time, 0.001)) * 0.20
            )
            * 1000,
            2,
        )

        # -------------------------------------------------------------
        # 2. Multi-Core Benchmark (Parallel execution across all cores)
        # -------------------------------------------------------------
        if cpu_count <= 1:
            # Single core machine: multi-core is identical to single-core
            multi_core_wall_time = single_core_total
            avg_mc_prime_time = sc_prime_time
            avg_mc_matrix_time = sc_matrix_time
            avg_mc_comp_time = sc_comp_time
            multi_core_score = single_core_score
            scaling_efficiency_pct = 100.0
        else:
            worker_args = [(prime_limit, matrix_size, compression_mb)] * cpu_count

            def _run_parallel() -> tuple[float, list[tuple[float, float, float]]]:
                start = time.perf_counter()
                with concurrent.futures.ProcessPoolExecutor(max_workers=cpu_count) as executor:
                    res = list(executor.map(_worker_composite_suite, worker_args))
                elapsed = time.perf_counter() - start
                return elapsed, res

            multi_core_wall_time, worker_results = await loop.run_in_executor(None, _run_parallel)

            # Calculate average per-task execution time across all parallel workers
            avg_mc_prime_time = sum(r[0] for r in worker_results) / cpu_count
            avg_mc_matrix_time = sum(r[1] for r in worker_results) / cpu_count
            avg_mc_comp_time = sum(r[2] for r in worker_results) / cpu_count

            # Multi-Core Score (Scales with N cores and parallel execution throughput)
            multi_core_score = round(
                (
                    ((REF_PRIME_TIME * cpu_count) / max(multi_core_wall_time * (sc_prime_time / max(single_core_total, 0.001)), 0.001)) * 0.45
                    + ((REF_MATRIX_TIME * cpu_count) / max(multi_core_wall_time * (sc_matrix_time / max(single_core_total, 0.001)), 0.001)) * 0.35
                    + ((REF_COMPRESSION_TIME * cpu_count) / max(multi_core_wall_time * (sc_comp_time / max(single_core_total, 0.001)), 0.001)) * 0.20
                )
                * 1000,
                2,
            )

            # Multi-core scaling efficiency relative to single-core
            scaling_efficiency_pct = round(
                (multi_core_score / max(single_core_score * cpu_count, 0.001)) * 100,
                1,
            )

        return {
            "single_core": {
                "prime_sieve_s": round(sc_prime_time, 4),
                "primes_found": prime_count,
                "matrix_multiply_s": round(sc_matrix_time, 4),
                "compression_s": round(sc_comp_time, 4),
                "total_time_s": round(single_core_total, 4),
            },
            "multi_core": {
                "workers": cpu_count,
                "total_time_s": round(multi_core_wall_time, 4),
                "avg_worker_prime_s": round(avg_mc_prime_time, 4),
                "avg_worker_matrix_s": round(avg_mc_matrix_time, 4),
                "avg_worker_comp_s": round(avg_mc_comp_time, 4),
                "scaling_efficiency_pct": scaling_efficiency_pct,
            },
            "scores": {
                "single_core": single_core_score,
                "multi_core": multi_core_score,
            },
        }
