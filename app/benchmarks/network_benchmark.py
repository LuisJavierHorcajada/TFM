"""
Network Benchmark Plugin.

Tests:
  - Internet speed (download/upload via speedtest-cli)
  - Ping latency (ICMP to configurable target)
  - DNS resolution time
"""

import asyncio
import re
import socket
import subprocess
import time

from app.services.base import Benchmark, BenchmarkInfo

# --- Benchmark Configuration ---
PING_HOST = "8.8.8.8"
PING_COUNT = 5
PING_TIMEOUT = 5
DNS_DOMAINS = ["google.com", "github.com", "cloudflare.com"]
# -------------------------------


def _ping_latency(host: str, count: int) -> dict:
    """Ping a host and parse the average RTT. Returns a dictionary with the stats."""
    try:
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", str(PING_TIMEOUT), host],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout

        # Parse: rtt min/avg/max/mdev = 1.234/5.678/9.012/3.456 ms
        match = re.search(
            r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)", output
        )
        if match:
            return {
                "host": host,
                "min_ms": float(match.group(1)),
                "avg_ms": float(match.group(2)),
                "max_ms": float(match.group(3)),
                "mdev_ms": float(match.group(4)),
                "packets_sent": count,
            }

        # Try to parse packet loss
        loss_match = re.search(r"(\d+)% packet loss", output)
        return {
            "host": host,
            "error": "Could not parse RTT",
            "packet_loss_percent": int(loss_match.group(1)) if loss_match else 100,
            "raw_output": output[:500],
        }

    except subprocess.TimeoutExpired:
        return {"host": host, "error": "Ping timed out"}
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

    avg = []
    for v in results.values():
        if isinstance(v, (int, float)):
            avg.append(v)
    return {
        "domains": results,
        "avg_ms": round(sum(avg) / len(avg), 3) if avg else None,
    }


def _speedtest() -> dict:
    """Run a speed test using speedtest. Returns download/upload Mbps."""
    try:
        import speedtest

        st = speedtest.Speedtest()
        st.get_best_server()

        download = st.download() / 1000000  # bits/s -> Mbps
        upload = st.upload() / 1000000

        server = st.best
        return {
            "download_mbps": round(download, 2),
            "upload_mbps": round(upload, 2),
            "server": {
                "name": server.get("name", ""),
                "country": server.get("country", ""),
                "sponsor": server.get("sponsor", ""),
                "latency_ms": round(server.get("latency", 0), 2),
            },
        }
    except Exception as e:
        return {"error": str(e)}


class NetworkBenchmark(Benchmark):
    """Network and internet performance benchmark."""

    @property
    def info(self) -> BenchmarkInfo:
        return BenchmarkInfo(
            name="network_benchmark",
            display_name="Network Benchmark",
            description="Tests internet download/upload speed, ping latency, and DNS resolution time.",
            category="network",
        )

    async def run(self, params: dict | None = None) -> dict:
        p = params or {}
        ping_host = p.get("ping_host", PING_HOST)
        ping_count = p.get("ping_count", PING_COUNT)
        dns_domains = p.get("dns_domains", None)
        skip_speedtest = p.get("skip_speedtest", False)

        loop = asyncio.get_event_loop()

        # 1. Ping latency
        ping_result = await loop.run_in_executor(
            None, _ping_latency, ping_host, ping_count
        )

        # 2. DNS resolution
        dns_result = await loop.run_in_executor(None, _dns_resolution, dns_domains)

        # 3. Internet speed test (can be slow, allow skipping)
        if skip_speedtest:
            speed_result = {"skipped": True}
        else:
            speed_result = await loop.run_in_executor(None, _speedtest)

        return {
            "ping": ping_result,
            "dns": dns_result,
            "speedtest": speed_result,
        }
