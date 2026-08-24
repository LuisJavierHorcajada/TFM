"""
Network Benchmark Plugin.

Tests:
  - ICMP ping latency (min/avg/max/mdev, jitter, packet loss)
  - DNS resolution latency
  - HTTP Download & Upload throughput via Cloudflare Speedtest CDN
"""

import asyncio
import logging
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request

from app.services.base import Benchmark, BenchmarkInfo

logger = logging.getLogger("esi_bench.network")

# --- Benchmark Configuration ---
PING_HOST = "8.8.8.8"
PING_COUNT = 5
PING_TIMEOUT = 5
DNS_DOMAINS = ["google.com", "cloudflare.com", "github.com"]

# Speed test endpoints (Cloudflare Speedtest CDN)
DOWNLOAD_URL = "https://speed.cloudflare.com/__down?bytes=25000000"  # 25 MB
UPLOAD_URL = "https://speed.cloudflare.com/__up"
UPLOAD_SIZE_BYTES = 5_000_000  # 5 MB
# -------------------------------


def _ping_latency(host: str, count: int) -> dict:
    """Ping a host and parse RTT, jitter, and packet loss stats."""
    try:
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", str(PING_TIMEOUT), host],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout

        # Parse individual times for inter-packet jitter (RFC 1889)
        times = [float(t) for t in re.findall(r"time=([\d.]+)\s*ms", output)]
        jitter_ms = 0.0
        if len(times) > 1:
            diffs = [abs(times[i] - times[i - 1]) for i in range(1, len(times))]
            jitter_ms = round(sum(diffs) / len(diffs), 3)

        # Parse packet transmission and loss
        loss_match = re.search(r"(\d+(?:\.\d+)?)%\s*(?:packet\s*)?loss", output)
        packet_loss = float(loss_match.group(1)) if loss_match else (0.0 if times else 100.0)

        stats_match = re.search(
            r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets\s+)?received", output
        )
        packets_sent = int(stats_match.group(1)) if stats_match else count
        packets_received = int(stats_match.group(2)) if stats_match else len(times)

        # Parse RTT stats (min/avg/max/mdev)
        rtt_match = re.search(
            r"(?:rtt|round-trip)\s+min/avg/max/(?:mdev|stddev)\s*=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)",
            output,
        )
        if rtt_match:
            mdev = float(rtt_match.group(4))
            return {
                "host": host,
                "min_ms": float(rtt_match.group(1)),
                "avg_ms": float(rtt_match.group(2)),
                "max_ms": float(rtt_match.group(3)),
                "mdev_ms": mdev,
                "jitter_ms": jitter_ms if jitter_ms > 0 else mdev,
                "packet_loss_percent": packet_loss,
                "packets_sent": packets_sent,
                "packets_received": packets_received,
            }

        if times:
            avg_ms = round(sum(times) / len(times), 3)
            return {
                "host": host,
                "min_ms": min(times),
                "avg_ms": avg_ms,
                "max_ms": max(times),
                "mdev_ms": jitter_ms,
                "jitter_ms": jitter_ms,
                "packet_loss_percent": packet_loss,
                "packets_sent": packets_sent,
                "packets_received": packets_received,
            }

        return {
            "host": host,
            "error": "Could not parse RTT",
            "packet_loss_percent": packet_loss,
            "packets_sent": packets_sent,
            "packets_received": packets_received,
            "raw_output": output[:500],
        }

    except subprocess.TimeoutExpired:
        return {"host": host, "error": "Ping timed out", "packet_loss_percent": 100.0}
    except FileNotFoundError:
        return {"host": host, "error": "ping command not found"}
    except Exception as e:
        return {"host": host, "error": str(e)}


def _dns_resolution(domains: list[str] | None = None) -> dict:
    """Measure DNS resolution time for common domains."""
    if domains is None:
        domains = DNS_DOMAINS

    results = {}
    for domain in domains:
        start = time.perf_counter()
        try:
            socket.getaddrinfo(domain, 80)
            elapsed_ms = (time.perf_counter() - start) * 1000
            results[domain] = round(elapsed_ms, 3)
        except socket.gaierror as e:
            results[domain] = f"error: {e}"

    avg = [v for v in results.values() if isinstance(v, (int, float))]
    return {
        "domains": results,
        "avg_ms": round(sum(avg) / len(avg), 3) if avg else None,
    }


def _http_speedtest() -> dict:
    """Measure download and upload throughput via HTTP."""
    download_mbps = None
    upload_mbps = None
    err = None

    # 1. Download test (25 MB)
    try:
        req = urllib.request.Request(
            DOWNLOAD_URL,
            headers={"User-Agent": "ESI-Bench/1.0"},
        )
        start = time.perf_counter()
        total_bytes = 0
        with urllib.request.urlopen(req, timeout=30) as resp:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
        elapsed = time.perf_counter() - start
        if elapsed > 0 and total_bytes > 0:
            download_mbps = round((total_bytes * 8) / (elapsed * 1_000_000), 2)
    except Exception as e:
        logger.warning("HTTP download test failed: %s", e)
        err = str(e)

    # 2. Upload test (5 MB)
    try:
        upload_data = os.urandom(UPLOAD_SIZE_BYTES)
        req = urllib.request.Request(
            UPLOAD_URL,
            data=upload_data,
            headers={
                "User-Agent": "ESI-Bench/1.0",
                "Content-Type": "application/octet-stream",
            },
            method="POST",
        )
        start = time.perf_counter()
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        elapsed = time.perf_counter() - start
        if elapsed > 0:
            upload_mbps = round((UPLOAD_SIZE_BYTES * 8) / (elapsed * 1_000_000), 2)
    except Exception as e:
        logger.warning("HTTP upload test failed: %s", e)
        if not err:
            err = str(e)

    result = {
        "download_mbps": download_mbps if download_mbps is not None else 0.0,
        "upload_mbps": upload_mbps if upload_mbps is not None else 0.0,
        "provider": "Cloudflare Speedtest CDN",
    }
    if err and download_mbps is None and upload_mbps is None:
        result["error"] = err

    return result


class NetworkBenchmark(Benchmark):
    """Network latency, jitter, packet loss, DNS resolution, and throughput benchmark."""

    @property
    def info(self) -> BenchmarkInfo:
        return BenchmarkInfo(
            name="network_benchmark",
            display_name="Network Benchmark",
            description="Tests internet download/upload speed, ping latency, jitter, packet loss, and DNS resolution time.",
            category="network",
        )

    async def run(self, params: dict | None = None) -> dict:
        p = params or {}
        ping_host = p.get("ping_host", PING_HOST)
        ping_count = p.get("ping_count", PING_COUNT)
        dns_domains = p.get("dns_domains", None)
        skip_speedtest = p.get("skip_speedtest", False)

        loop = asyncio.get_event_loop()

        # 1. Ping latency, jitter, packet loss
        ping_result = await loop.run_in_executor(
            None, _ping_latency, ping_host, ping_count
        )

        # 2. DNS resolution
        dns_result = await loop.run_in_executor(None, _dns_resolution, dns_domains)

        # 3. Internet speed test
        if skip_speedtest:
            speed_result = {"skipped": True}
        else:
            speed_result = await loop.run_in_executor(None, _http_speedtest)

        return {
            "ping": ping_result,
            "dns": dns_result,
            "speedtest": speed_result,
        }
