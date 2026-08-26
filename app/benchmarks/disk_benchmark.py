"""
Disk I/O Benchmark Plugin.

Tests:
  - Sequential write (MB/s)
  - Sequential read (MB/s)
  - Random write IOPS
  - Random read IOPS
"""

import asyncio
import logging
import mmap
import os
import random
import time
from pathlib import Path

from app.config import settings
from app.services.base import Benchmark, BenchmarkInfo

logger = logging.getLogger("esi_bench.disk")

# --- Benchmark Configuration ---
SIZE_MB = 1024
RANDOM_OPS = 5000
SEQ_BLOCK_SIZE = 1024 * 1024  # 1MB
RAND_BLOCK_SIZE = 4096        # 4KB
ALIGNMENT = 4096
# -------------------------------

O_DIRECT = getattr(os, "O_DIRECT", 0)


def _detect_filesystem(path: str) -> dict:
    """Inspect /proc/mounts to identify the underlying filesystem and persistence."""
    real_path = os.path.realpath(os.path.abspath(path))
    check_path = real_path
    while not os.path.exists(check_path) and check_path != "/":
        check_path = os.path.dirname(check_path)

    mount_point = "/"
    device = "unknown"
    fs_type = "unknown"

    try:
        if os.path.exists("/proc/mounts"):
            with open("/proc/mounts", "r") as f:
                best_len = -1
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3:
                        dev, mnt, fst = parts[0], parts[1], parts[2]
                        if check_path == mnt or check_path.startswith(mnt.rstrip("/") + "/"):
                            if len(mnt) > best_len:
                                best_len = len(mnt)
                                device, mount_point, fs_type = dev, mnt, fst
    except Exception:
        pass

    non_persistent_types = ("tmpfs", "ramfs", "overlay", "overlayfs", "shm", "devtmpfs")
    is_persistent = fs_type not in non_persistent_types and fs_type != "unknown"

    if not is_persistent:
        logger.warning(
            "Disk benchmark directory '%s' is on '%s' (non-persistent/container overlay). "
            "Mount a persistent volume (e.g. named volume or host directory) for accurate results.",
            real_path,
            fs_type,
        )
    else:
        logger.info("Disk benchmark target: %s on device %s (%s)", real_path, device, fs_type)

    return {
        "path": real_path,
        "filesystem": fs_type,
        "mount_point": mount_point,
        "device": device,
        "is_persistent": is_persistent,
    }


def _create_direct_buffer(size: int, fill: bytes | None = None) -> mmap.mmap:
    """Create a page-aligned memory buffer suitable for O_DIRECT."""
    buf = mmap.mmap(-1, size)
    if fill:
        fill_len = len(fill)
        for i in range(0, size, fill_len):
            chunk = min(fill_len, size - i)
            buf[i : i + chunk] = fill[:chunk]
    return buf


def _sequential_write(file_path: str, size_mb: int) -> float:
    """Write a file sequentially using direct I/O (with standard I/O fallback). Returns MB/s."""
    total_bytes = size_mb * 1024 * 1024
    pattern = os.urandom(ALIGNMENT)
    buf = _create_direct_buffer(SEQ_BLOCK_SIZE, fill=pattern)

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = None

    # Try O_DIRECT first
    if O_DIRECT:
        try:
            fd = os.open(file_path, flags | O_DIRECT, 0o666)
        except OSError:
            fd = None

    # Fallback to standard open
    if fd is None:
        fd = os.open(file_path, flags, 0o666)

    written = 0
    start = time.perf_counter()
    try:
        while written < total_bytes:
            try:
                os.write(fd, buf)
            except OSError as e:
                # If O_DIRECT write fails (e.g. EINVAL on virtualized disks), retry without O_DIRECT
                logger.debug("Direct write failed (%s), falling back to standard write", e)
                os.close(fd)
                fd = os.open(file_path, flags, 0o666)
                while written < total_bytes:
                    os.write(fd, buf)
                    written += SEQ_BLOCK_SIZE
                break
            written += SEQ_BLOCK_SIZE
        os.fsync(fd)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        buf.close()

    elapsed = time.perf_counter() - start
    return round(size_mb / max(elapsed, 0.0001), 2)


def _sequential_read(file_path: str) -> float:
    """Read a file sequentially using direct I/O + page-cache invalidation. Returns MB/s."""
    file_size = os.path.getsize(file_path)
    buf = _create_direct_buffer(SEQ_BLOCK_SIZE)

    flags = os.O_RDONLY
    fd = None
    use_direct = False

    if O_DIRECT:
        try:
            fd = os.open(file_path, flags | O_DIRECT)
            use_direct = True
        except OSError:
            fd = None

    if fd is None:
        fd = os.open(file_path, flags)

    try:
        # Invalidate page cache before reading
        if hasattr(os, "posix_fadvise"):
            try:
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            except OSError:
                pass

        start = time.perf_counter()
        total_read = 0
        while total_read < file_size:
            try:
                # Use readv to read directly into aligned mmap buffer (required for O_DIRECT)
                n = os.readv(fd, [buf])
                if not n:
                    break
                total_read += n
            except OSError as e:
                # Fallback to standard read if direct readv errors
                logger.debug("Direct readv failed (%s), falling back to standard read", e)
                os.close(fd)
                fd = os.open(file_path, flags)
                if hasattr(os, "posix_fadvise"):
                    try:
                        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                    except OSError:
                        pass
                while total_read < file_size:
                    chunk = os.read(fd, SEQ_BLOCK_SIZE)
                    if not chunk:
                        break
                    total_read += len(chunk)
                break
        elapsed = time.perf_counter() - start
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        buf.close()

    size_mb = file_size / (1024 * 1024)
    return round(size_mb / max(elapsed, 0.0001), 2)


def _random_write_iops(file_path: str, file_size: int, num_ops: int) -> float:
    """Write blocks at random 4KB-aligned offsets. Returns IOPS."""
    random.seed(42)
    max_blocks = max((file_size - RAND_BLOCK_SIZE) // RAND_BLOCK_SIZE, 1)
    offsets = [random.randint(0, max_blocks - 1) * RAND_BLOCK_SIZE for _ in range(num_ops)]

    buf = _create_direct_buffer(RAND_BLOCK_SIZE, fill=os.urandom(RAND_BLOCK_SIZE))
    flags = os.O_RDWR
    fd = None

    if O_DIRECT:
        try:
            fd = os.open(file_path, flags | O_DIRECT)
        except OSError:
            fd = None

    if fd is None:
        fd = os.open(file_path, flags)

    start = time.perf_counter()
    try:
        for offset in offsets:
            os.lseek(fd, offset, os.SEEK_SET)
            try:
                os.write(fd, buf)
            except OSError:
                # Fallback to standard I/O if direct write fails
                os.close(fd)
                fd = os.open(file_path, flags)
                os.lseek(fd, offset, os.SEEK_SET)
                os.write(fd, buf)
        os.fsync(fd)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        buf.close()

    elapsed = time.perf_counter() - start
    return round(num_ops / max(elapsed, 0.0001), 2)


def _random_read_iops(file_path: str, file_size: int, num_ops: int) -> float:
    """Read blocks from random 4KB-aligned offsets into aligned buffer. Returns IOPS."""
    random.seed(42)
    max_blocks = max((file_size - RAND_BLOCK_SIZE) // RAND_BLOCK_SIZE, 1)
    offsets = [random.randint(0, max_blocks - 1) * RAND_BLOCK_SIZE for _ in range(num_ops)]

    buf = _create_direct_buffer(RAND_BLOCK_SIZE)
    flags = os.O_RDONLY
    fd = None

    if O_DIRECT:
        try:
            fd = os.open(file_path, flags | O_DIRECT)
        except OSError:
            fd = None

    if fd is None:
        fd = os.open(file_path, flags)

    try:
        if hasattr(os, "posix_fadvise"):
            try:
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            except OSError:
                pass

        start = time.perf_counter()
        for offset in offsets:
            os.lseek(fd, offset, os.SEEK_SET)
            try:
                os.readv(fd, [buf])
            except OSError:
                # Fallback to standard read if direct read fails
                os.read(fd, RAND_BLOCK_SIZE)
        elapsed = time.perf_counter() - start
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        buf.close()

    return round(num_ops / max(elapsed, 0.0001), 2)


class DiskBenchmark(Benchmark):
    """Disk benchmark with direct I/O support."""

    @property
    def info(self) -> BenchmarkInfo:
        return BenchmarkInfo(
            name="disk_benchmark",
            display_name="Disk I/O Benchmark",
            description="Tests sequential and random read/write performance and IOPS using direct I/O.",
            category="disk",
        )

    async def run(self, params: dict | None = None) -> dict:
        p = params or {}
        size_mb = p.get("size_mb", SIZE_MB)
        random_ops = p.get("random_ops", RANDOM_OPS)

        # 1. Setup - Create temp file path and inspect filesystem persistence
        bench_dir = Path(settings.BENCHMARK_DISK_PATH)
        bench_dir.mkdir(parents=True, exist_ok=True)
        test_file = str(bench_dir / "benchmark_test.bin")
        fs_info = _detect_filesystem(str(bench_dir))

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

            # Compute normalized score (1000 = baseline of 100MB/s r/w and 500 IOPS)
            disk_score = round(
                (
                    (seq_read / 100.0) * 0.35
                    + (seq_write / 100.0) * 0.35
                    + (rand_read / 500.0) * 0.15
                    + (rand_write / 500.0) * 0.15
                )
                * 1000,
                2,
            )

            return {
                "storage_info": {
                    "filesystem": fs_info["filesystem"],
                    "mount_point": fs_info["mount_point"],
                    "device": fs_info["device"],
                    "is_persistent": fs_info["is_persistent"],
                },
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
                "scores": {
                    "disk_score": disk_score,
                },
            }
        finally:
            # Clean up temp file
            try:
                if os.path.exists(test_file):
                    os.remove(test_file)
            except OSError:
                pass
