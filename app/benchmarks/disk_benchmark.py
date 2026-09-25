"""
Disk I/O Benchmark Plugin.

Tests:
- Sequential write throughput (MiB/s)
- Sequential read throughput (MiB/s)
- Random write IOPS
- Random read IOPS

The benchmark attempts to use O_DIRECT. If direct I/O is unsupported
or fails, the affected test is restarted completely using buffered I/O.

Each result reports the actual I/O mode used, preventing direct and
buffered results from being treated as equivalent.
"""

import asyncio
import errno
import logging
import mmap
import os
import random
import time
from pathlib import Path
from typing import Callable, TypeVar

from app.config import settings
from app.services.base import Benchmark, BenchmarkInfo

logger = logging.getLogger("esi_bench.disk")

# Default profile
SIZE_MB = 1024
RANDOM_OPS = 5000

# Block sizes
SEQ_BLOCK_SIZE = 1024 * 1024  # 1 MiB
RAND_BLOCK_SIZE = 4096  # 4 KiB
ALIGNMENT = 4096

O_DIRECT = getattr(os, "O_DIRECT", 0)

T = TypeVar("T")


def _detect_filesystem(path: str) -> dict:
    """Inspect /proc/mounts and identify the filesystem backing path."""
    real_path = os.path.realpath(os.path.abspath(path))
    check_path = real_path

    while not os.path.exists(check_path) and check_path != "/":
        check_path = os.path.dirname(check_path)

    mount_point = "/"
    device = "unknown"
    fs_type = "unknown"

    try:
        if os.path.exists("/proc/mounts"):
            with open("/proc/mounts", "r", encoding="utf-8") as mounts:
                best_length = -1

                for line in mounts:
                    parts = line.split()
                    if len(parts) < 3:
                        continue

                    current_device, current_mount, current_fs = parts[:3]

                    belongs_to_mount = (
                        check_path == current_mount
                        or check_path.startswith(current_mount.rstrip("/") + "/")
                    )

                    if belongs_to_mount and len(current_mount) > best_length:
                        best_length = len(current_mount)
                        device = current_device
                        mount_point = current_mount
                        fs_type = current_fs
    except OSError as exc:
        logger.warning("Could not inspect /proc/mounts: %s", exc)

    non_persistent_types = {
        "tmpfs",
        "ramfs",
        "overlay",
        "overlayfs",
        "shm",
        "devtmpfs",
    }

    is_persistent = (
        fs_type != "unknown"
        and fs_type not in non_persistent_types
    )

    if not is_persistent:
        logger.warning(
            "Benchmark directory '%s' is backed by filesystem '%s'. "
            "Results may represent a container overlay or non-persistent storage.",
            real_path,
            fs_type,
        )

    logger.info(
        "Disk benchmark target: path=%s device=%s filesystem=%s",
        real_path,
        device,
        fs_type,
    )

    return {
        "path": real_path,
        "filesystem": fs_type,
        "mount_point": mount_point,
        "device": device,
        "is_persistent": is_persistent,
    }


def _create_aligned_buffer(
    size: int,
    fill: bytes | None = None,
) -> mmap.mmap:
    """
    Create a page-aligned anonymous mmap buffer suitable for O_DIRECT.
    """
    if size % ALIGNMENT != 0:
        raise ValueError(
            f"Buffer size {size} must be aligned to {ALIGNMENT} bytes"
        )

    buffer = mmap.mmap(-1, size)

    if fill:
        fill_length = len(fill)

        for offset in range(0, size, fill_length):
            chunk_size = min(fill_length, size - offset)
            buffer[offset:offset + chunk_size] = fill[:chunk_size]

    return buffer


def _is_direct_io_fallback_error(exc: OSError) -> bool:
    """
    Return True when the error is compatible with unsupported or invalid
    direct I/O usage.
    """
    return exc.errno in {
        errno.EINVAL,
        errno.ENOTSUP,
        errno.EOPNOTSUPP,
        errno.ENOSYS,
        errno.EPERM,
    }


def _drop_file_cache(fd: int) -> None:
    """
    Ask the operating system to discard cached pages associated with a file.

    This is advisory and does not guarantee that every cache layer is cleared.
    """
    if not hasattr(os, "posix_fadvise"):
        return

    try:
        os.posix_fadvise(
            fd,
            0,
            0,
            os.POSIX_FADV_DONTNEED,
        )
    except (AttributeError, OSError):
        pass


def _write_full_block(
    fd: int,
    buffer: mmap.mmap,
    direct_io: bool,
) -> None:
    """
    Write one complete block.

    Partial direct writes are treated as an error because continuing from a
    non-aligned offset could invalidate O_DIRECT alignment requirements.
    """
    expected = len(buffer)
    written = os.write(fd, buffer)

    if written == expected:
        return

    if direct_io:
        raise OSError(
            errno.EIO,
            f"Partial direct write: {written}/{expected} bytes",
        )

    remaining = memoryview(buffer)[written:]

    try:
        while remaining:
            count = os.write(fd, remaining)
            if count <= 0:
                raise OSError(errno.EIO, "Buffered write returned zero bytes")
            remaining = remaining[count:]
    finally:
        remaining.release()


def _run_with_io_fallback(
    direct_operation: Callable[[], T],
    buffered_operation: Callable[[], T],
    operation_name: str,
) -> tuple[T, dict]:
    """
    Attempt an operation with O_DIRECT and restart it using buffered I/O
    if direct I/O is unavailable or fails.

    The buffered execution is timed independently, so the failed direct
    attempt is not included in the reported metric.
    """
    if not O_DIRECT:
        result = buffered_operation()
        return result, {
            "requested_mode": "direct",
            "used_mode": "buffered",
            "direct_io_supported_by_python": False,
            "fallback_used": True,
            "fallback_reason": "os.O_DIRECT is not available",
        }

    try:
        result = direct_operation()
        return result, {
            "requested_mode": "direct",
            "used_mode": "direct",
            "direct_io_supported_by_python": True,
            "fallback_used": False,
            "fallback_reason": None,
        }
    except OSError as exc:
        logger.warning(
            "%s failed with O_DIRECT: %s. "
            "Restarting the complete test with buffered I/O.",
            operation_name,
            exc,
        )

        result = buffered_operation()

        return result, {
            "requested_mode": "direct",
            "used_mode": "buffered",
            "direct_io_supported_by_python": True,
            "fallback_used": True,
            "fallback_reason": f"{type(exc).__name__}: {exc}",
        }


def _sequential_write_impl(
    file_path: str,
    size_mb: int,
    direct_io: bool,
) -> float:
    """Execute a sequential write test and return throughput in MiB/s."""
    total_bytes = size_mb * 1024 * 1024

    if total_bytes <= 0:
        raise ValueError("size_mb must be greater than zero")

    if total_bytes % SEQ_BLOCK_SIZE != 0:
        raise ValueError(
            "Sequential test size must be a multiple of the sequential block size"
        )

    pattern = os.urandom(ALIGNMENT)
    buffer = _create_aligned_buffer(SEQ_BLOCK_SIZE, fill=pattern)

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if direct_io:
        flags |= O_DIRECT

    fd: int | None = None

    try:
        fd = os.open(file_path, flags, 0o666)

        bytes_written = 0
        start = time.perf_counter()

        while bytes_written < total_bytes:
            _write_full_block(fd, buffer, direct_io)
            bytes_written += SEQ_BLOCK_SIZE

        os.fsync(fd)
        elapsed = time.perf_counter() - start

        actual_size = os.fstat(fd).st_size
        if actual_size != total_bytes:
            raise RuntimeError(
                f"Unexpected file size after sequential write: "
                f"{actual_size} bytes instead of {total_bytes}"
            )

        return round(
            (total_bytes / (1024 * 1024)) / max(elapsed, 0.000001),
            2,
        )
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

        buffer.close()


def _sequential_write(
    file_path: str,
    size_mb: int,
) -> tuple[float, dict]:
    """Run sequential write with direct I/O and buffered fallback."""
    return _run_with_io_fallback(
        direct_operation=lambda: _sequential_write_impl(
            file_path,
            size_mb,
            direct_io=True,
        ),
        buffered_operation=lambda: _sequential_write_impl(
            file_path,
            size_mb,
            direct_io=False,
        ),
        operation_name="Sequential write",
    )


def _sequential_read_impl(
    file_path: str,
    direct_io: bool,
) -> float:
    """Execute a sequential read test and return throughput in MiB/s."""
    file_size = os.path.getsize(file_path)

    if file_size <= 0:
        raise RuntimeError("Benchmark file is empty")

    buffer = _create_aligned_buffer(SEQ_BLOCK_SIZE)

    flags = os.O_RDONLY
    if direct_io:
        flags |= O_DIRECT

    fd: int | None = None

    try:
        fd = os.open(file_path, flags)
        _drop_file_cache(fd)

        total_read = 0
        start = time.perf_counter()

        while total_read < file_size:
            if direct_io:
                count = os.readv(fd, [buffer])
            else:
                chunk = os.read(fd, SEQ_BLOCK_SIZE)
                count = len(chunk)

            if count == 0:
                break

            total_read += count

        elapsed = time.perf_counter() - start

        if total_read != file_size:
            raise RuntimeError(
                f"Incomplete sequential read: "
                f"{total_read}/{file_size} bytes"
            )

        return round(
            (file_size / (1024 * 1024)) / max(elapsed, 0.000001),
            2,
        )
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

        buffer.close()


def _sequential_read(
    file_path: str,
) -> tuple[float, dict]:
    """Run sequential read with direct I/O and buffered fallback."""
    return _run_with_io_fallback(
        direct_operation=lambda: _sequential_read_impl(
            file_path,
            direct_io=True,
        ),
        buffered_operation=lambda: _sequential_read_impl(
            file_path,
            direct_io=False,
        ),
        operation_name="Sequential read",
    )


def _build_random_offsets(
    file_size: int,
    number_of_operations: int,
) -> list[int]:
    """Create a deterministic list of aligned random offsets."""
    if number_of_operations <= 0:
        raise ValueError("number_of_operations must be greater than zero")

    if file_size < RAND_BLOCK_SIZE:
        raise ValueError("Benchmark file is too small for random I/O")

    random_generator = random.Random(42)
    number_of_blocks = file_size // RAND_BLOCK_SIZE

    return [
        random_generator.randrange(number_of_blocks) * RAND_BLOCK_SIZE
        for _ in range(number_of_operations)
    ]


def _random_write_iops_impl(
    file_path: str,
    file_size: int,
    number_of_operations: int,
    offsets: list[int],
    direct_io: bool,
) -> float:
    """
    Execute random 4 KiB writes.

    One fsync is performed at the end of the complete batch. The result
    therefore represents batched random-write IOPS with final synchronization.
    """
    buffer = _create_aligned_buffer(
        RAND_BLOCK_SIZE,
        fill=os.urandom(RAND_BLOCK_SIZE),
    )

    flags = os.O_RDWR
    if direct_io:
        flags |= O_DIRECT

    fd: int | None = None

    try:
        fd = os.open(file_path, flags)
        start = time.perf_counter()

        for offset in offsets:
            os.lseek(fd, offset, os.SEEK_SET)
            _write_full_block(fd, buffer, direct_io)

        os.fsync(fd)
        elapsed = time.perf_counter() - start

        return round(
            number_of_operations / max(elapsed, 0.000001),
            2,
        )
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

        buffer.close()


def _random_write_iops(
    file_path: str,
    file_size: int,
    number_of_operations: int,
) -> tuple[float, dict]:
    """Run random write test with a deterministic offset sequence."""
    offsets = _build_random_offsets(file_size, number_of_operations)

    return _run_with_io_fallback(
        direct_operation=lambda: _random_write_iops_impl(
            file_path,
            file_size,
            number_of_operations,
            offsets,
            direct_io=True,
        ),
        buffered_operation=lambda: _random_write_iops_impl(
            file_path,
            file_size,
            number_of_operations,
            offsets,
            direct_io=False,
        ),
        operation_name="Random write",
    )


def _random_read_iops_impl(
    file_path: str,
    file_size: int,
    number_of_operations: int,
    offsets: list[int],
    direct_io: bool,
) -> float:
    """Execute random 4 KiB reads and return IOPS."""
    buffer = _create_aligned_buffer(RAND_BLOCK_SIZE)

    flags = os.O_RDONLY
    if direct_io:
        flags |= O_DIRECT

    fd: int | None = None

    try:
        fd = os.open(file_path, flags)
        _drop_file_cache(fd)

        start = time.perf_counter()

        for offset in offsets:
            os.lseek(fd, offset, os.SEEK_SET)

            if direct_io:
                count = os.readv(fd, [buffer])
            else:
                count = len(os.read(fd, RAND_BLOCK_SIZE))

            if count != RAND_BLOCK_SIZE:
                raise RuntimeError(
                    f"Incomplete random read at offset {offset}: "
                    f"{count}/{RAND_BLOCK_SIZE} bytes"
                )

        elapsed = time.perf_counter() - start

        return round(
            number_of_operations / max(elapsed, 0.000001),
            2,
        )
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

        buffer.close()


def _random_read_iops(
    file_path: str,
    file_size: int,
    number_of_operations: int,
) -> tuple[float, dict]:
    """Run random read test with a deterministic offset sequence."""
    offsets = _build_random_offsets(file_size, number_of_operations)

    return _run_with_io_fallback(
        direct_operation=lambda: _random_read_iops_impl(
            file_path,
            file_size,
            number_of_operations,
            offsets,
            direct_io=True,
        ),
        buffered_operation=lambda: _random_read_iops_impl(
            file_path,
            file_size,
            number_of_operations,
            offsets,
            direct_io=False,
        ),
        operation_name="Random read",
    )


class DiskBenchmark(Benchmark):
    """Disk benchmark with explicit direct and buffered I/O reporting."""

    @property
    def info(self) -> BenchmarkInfo:
        return BenchmarkInfo(
            name="disk_benchmark",
            display_name="Disk I/O Benchmark",
            description=(
                "Tests sequential and random read/write performance. "
                "O_DIRECT is attempted and any buffered fallback is reported."
            ),
            category="disk",
        )

    async def run(self, params: dict | None = None) -> dict:
        parameters = params or {}

        size_mb = int(parameters.get("size_mb", SIZE_MB))
        random_ops = int(parameters.get("random_ops", RANDOM_OPS))

        if size_mb <= 0:
            raise ValueError("size_mb must be greater than zero")

        if random_ops <= 0:
            raise ValueError("random_ops must be greater than zero")

        if size_mb > 16 * 1024:
            raise ValueError("size_mb exceeds the maximum allowed value")

        if random_ops > 1_000_000:
            raise ValueError("random_ops exceeds the maximum allowed value")

        benchmark_directory = Path(settings.BENCHMARK_DISK_PATH)
        benchmark_directory.mkdir(parents=True, exist_ok=True)

        test_file = str(benchmark_directory / "benchmark_test.bin")
        filesystem_info = _detect_filesystem(str(benchmark_directory))

        loop = asyncio.get_running_loop()

        try:
            sequential_write, sequential_write_io = await loop.run_in_executor(
                None,
                _sequential_write,
                test_file,
                size_mb,
            )

            actual_file_size = os.path.getsize(test_file)
            expected_file_size = size_mb * 1024 * 1024

            if actual_file_size != expected_file_size:
                raise RuntimeError(
                    f"Invalid benchmark file size: "
                    f"{actual_file_size} bytes instead of {expected_file_size}"
                )

            sequential_read, sequential_read_io = await loop.run_in_executor(
                None,
                _sequential_read,
                test_file,
            )

            random_write, random_write_io = await loop.run_in_executor(
                None,
                _random_write_iops,
                test_file,
                actual_file_size,
                random_ops,
            )

            random_read, random_read_io = await loop.run_in_executor(
                None,
                _random_read_iops,
                test_file,
                actual_file_size,
                random_ops,
            )

            modes = {
                sequential_write_io["used_mode"],
                sequential_read_io["used_mode"],
                random_write_io["used_mode"],
                random_read_io["used_mode"],
            }

            if len(modes) == 1:
                overall_mode = next(iter(modes))
            else:
                overall_mode = "mixed"

            return {
                "storage_info": {
                    "path": filesystem_info["path"],
                    "filesystem": filesystem_info["filesystem"],
                    "mount_point": filesystem_info["mount_point"],
                    "device": filesystem_info["device"],
                    "is_persistent": filesystem_info["is_persistent"],
                    "overall_io_mode": overall_mode,
                },
                "sequential_write": {
                    "size_mb": size_mb,
                    "speed_mib_s": sequential_write,
                    "io": sequential_write_io,
                },
                "sequential_read": {
                    "size_mb": size_mb,
                    "speed_mib_s": sequential_read,
                    "io": sequential_read_io,
                },
                "random_write": {
                    "operations": random_ops,
                    "block_size_bytes": RAND_BLOCK_SIZE,
                    "iops": random_write,
                    "synchronization": "single fsync after complete batch",
                    "io": random_write_io,
                },
                "random_read": {
                    "operations": random_ops,
                    "block_size_bytes": RAND_BLOCK_SIZE,
                    "iops": random_read,
                    "io": random_read_io,
                },
            }
        finally:
            try:
                if os.path.exists(test_file):
                    os.remove(test_file)
            except OSError as exc:
                logger.warning(
                    "Could not remove disk benchmark file '%s': %s",
                    test_file,
                    exc,
                )
