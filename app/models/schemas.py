"""
ESI-Bench - Pydantic schemas for requests, responses and database results.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

class RunRequest(BaseModel):
    """Request to start a benchmark run."""

    benchmarks: list[str] = Field(
        ...,
        description='List of benchmark names to run, or ["all"] for everything.',
        examples=[["cpu_benchmark", "memory_benchmark"], ["all"]],
    )
    params: dict[str, Any] | None = Field(
        default=None,
        description="Optional parameters passed to each benchmark's run() method.",
    )


class RunResponse(BaseModel):
    """Immediate response after starting a benchmark run."""

    run_id: str
    status: str = "pending"
    benchmarks: list[str]


class RunStatus(BaseModel):
    """Status of an in-progress or completed benchmark run."""

    run_id: str
    status: Literal["pending", "running", "completed", "failed"]
    progress: str = ""  # e.g. "2/4"
    current_benchmark: str = ""
    error: str | None = None


class SystemInfo(BaseModel):
    """Platform data collected before each benchmark execution."""

    hostname: str
    os: str
    os_version: str
    cpu_model: str
    cpu_count: int
    ram_total_gb: float
    ram_available_gb: float
    python_version: str


class BenchmarkSummary(BaseModel):
    """Information about a benchmark."""

    name: str
    display_name: str
    description: str
    category: str


class BenchmarkResultDoc(BaseModel):
    """Full benchmark result document stored in MongoDB."""

    run_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    system_info: SystemInfo | None = None
    benchmarks_requested: list[str] = []
    results: dict[str, Any] = {}  # benchmark_name -> its result dict
    duration_s: float = 0.0
    error: str | None = None
    progress: str = ""
    current_benchmark: str = ""
