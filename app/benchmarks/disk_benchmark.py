"""
Disk I/O Benchmark Plugin.

Tests:
  - Sequential write (MB/s)
  - Sequential read (MB/s)
  - Random write IOPS
  - Random read IOPS
"""

import asyncio
import os
import random
import time
from pathlib import Path

from app.config import settings
from app.services.base import Benchmark, BenchmarkInfo

# --- Benchmark Configuration ---
SIZE_MB = 256
RANDOM_OPS = 1000
SEQ_BLOCK_SIZE = 1024 * 1024  # 1MB
RAND_BLOCK_SIZE = 4096        # 4KB
# -------------------------------


def _sequential_write(file_path: str, size_mb: int) -> float:
    """Write a file sequentially. Returns MB/s."""
    data = os.urandom(SEQ_BLOCK_SIZE)
    total_bytes = size_mb * 1024 * 1024
    written = 0

    start = time.perf_counter()
    with open(file_path, "wb") as f:
        while written < total_bytes:
            f.write(data)
            written += SEQ_BLOCK_SIZE
        f.flush()
        os.fsync(f.fileno())
    elapsed = time.perf_counter() - start

    return round(size_mb / max(elapsed, 0.0001), 2)


def _sequential_read(file_path: str) -> float:
    """Read a file sequentially. Returns MB/s."""
    file_size = os.path.getsize(file_path)

    start = time.perf_counter()
    with open(file_path, "rb") as f:
        while f.read(SEQ_BLOCK_SIZE):
            pass
    elapsed = time.perf_counter() - start

    size_mb = file_size / (1024 * 1024)
    return round(size_mb / max(elapsed, 0.0001), 2)


def _random_write_iops(file_path: str, file_size: int, num_ops: int) -> float:
    """Write blocks at random offsets. Returns IOPS."""
    random.seed(42)
    max_offset = max(file_size - RAND_BLOCK_SIZE, 0)
    data = os.urandom(RAND_BLOCK_SIZE)
    offsets = []
    for i in range(num_ops):
        offsets.append(random.randint(0, max_offset))

    start = time.perf_counter()
    with open(file_path, "r+b") as f:
        for offset in offsets:
            f.seek(offset)
            f.write(data)
        f.flush()
        os.fsync(f.fileno())
    elapsed = time.perf_counter() - start

    return round(num_ops / max(elapsed, 0.0001), 2)


def _random_read_iops(file_path: str, file_size: int, num_ops: int) -> float:
    """Read blocks from random offsets. Returns IOPS."""
    random.seed(42)
    max_offset = max(file_size - RAND_BLOCK_SIZE, 0)
    offsets = []
    for i in range(num_ops):
        offsets.append(random.randint(0, max_offset))

    start = time.perf_counter()
    with open(file_path, "rb") as f:
        for offset in offsets:
            f.seek(offset)
            f.read(RAND_BLOCK_SIZE)
    elapsed = time.perf_counter() - start

    return round(num_ops / max(elapsed, 0.0001), 2)


class DiskBenchmark(Benchmark):
    """Disk benchmark."""

    @property
    def info(self) -> BenchmarkInfo:
        return BenchmarkInfo(
            name="disk_benchmark",
            display_name="Disk I/O Benchmark",
            description="Tests sequential and random read/write performance and IOPS.",
            category="disk",
        )

    async def run(self, params: dict | None = None) -> dict:
        p = params or {}
        size_mb = p.get("size_mb", SIZE_MB)
        random_ops = p.get("random_ops", RANDOM_OPS)

        # 1. Setup - Create temp file path
        bench_dir = Path(settings.BENCHMARK_DISK_PATH)
        bench_dir.mkdir(parents=True, exist_ok=True)
        test_file = str(bench_dir / "benchmark_test.bin")

        try:
            loop = asyncio.get_event_loop()

            # 2. Sequential write
            seq_write = await loop.run_in_executor(
                None, _sequential_write, test_file, size_mb
            )

            # 3. Sequential read
            seq_read = await loop.run_in_executor(
                None, _sequential_read, test_file
            )

            # 4. Random write IOPS
            file_size = os.path.getsize(test_file)
            rand_write = await loop.run_in_executor(
                None, _random_write_iops, test_file, file_size, random_ops
            )

            # 5. Random read IOPS
            rand_read = await loop.run_in_executor(
                None, _random_read_iops, test_file, file_size, random_ops
            )

            return {
                "sequential_write": {
                    "size_mb": size_mb,
                    "speed_mb_s": seq_write,
                },
                "sequential_read": {
                    "size_mb": size_mb,
                    "speed_mb_s": seq_read,
                },
                "random_write": {
                    "ops": random_ops,
                    "iops": rand_write,
                },
                "random_read": {
                    "ops": random_ops,
                    "iops": rand_read,
                },
            }
        finally:
            # 6. Removes temp file
            try:
                if os.path.exists(test_file):
                    os.remove(test_file)
            except OSError:
                pass
