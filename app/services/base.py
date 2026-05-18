"""
ESI-Bench - Base class for all benchmark plugins.

Every benchmark must extend BaseBenchmark and implement:

"""

from abc import ABC, abstractmethod

from pydantic import BaseModel


class BenchmarkInfo(BaseModel):
    """Information about the benchmark."""

    name: str
    display_name: str
    description: str
    category: str


class Benchmark(ABC):
    """Abstract base class for benchmark plugins."""

    @property
    @abstractmethod
    def info(self) -> BenchmarkInfo:
        """Returns information about the benchmark."""

    @abstractmethod
    async def run(self, params: dict | None = None) -> dict:
        """Execute the benchmark."""

