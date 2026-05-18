"""
ESI-Bench - Plugin registry.

Scans the app/benchmarks/ directory, imports every module,
finds all Benchmark subclasses, and registers them by name.
"""

import importlib
import inspect
import pkgutil
from pathlib import Path

from app.services.base import Benchmark


class BenchmarkRegistry:
    """Discovers and manages benchmark plugins."""

    def __init__(self):
        self.benchmarks: dict[str, Benchmark] = {}

    def discover(self) -> None:
        """Scan app/benchmarks/ and register all Benchmark subclasses."""
        benchmarks_package = "app.benchmarks"
        benchmarks_dir = Path(__file__).parent.parent / "benchmarks"

        if not benchmarks_dir.exists():
            print(f"Warning: Benchmarks directory not found: {benchmarks_dir}")
            return

        for importer, module_name, is_pkg in pkgutil.iter_modules([str(benchmarks_dir)]):
            if module_name.startswith("_"):
                continue

            full_module_name = f"{benchmarks_package}.{module_name}"
            try:
                module = importlib.import_module(full_module_name)
            except Exception as e:
                print(f"Warning: Failed to import {full_module_name}: {e}")
                continue

            # Find all Benchmark subclasses in the module
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, Benchmark)
                    and obj is not Benchmark
                    and not inspect.isabstract(obj)
                ):
                    try:
                        instance = obj()
                        info = instance.info
                        if info.name in self.benchmarks:
                            print(
                                f"Warning: Duplicate benchmark name '{info.name}' "
                                f"from {full_module_name}, skipping"
                            )
                            continue
                        self.benchmarks[info.name] = instance
                        print(f"Registered: {info.display_name} ({info.category})")
                    except Exception as e:
                        print(f"Warning: Failed to register {name} from {full_module_name}: {e}")

    def list_benchmarks(self) -> list[dict]:
        """Return info for all registered benchmarks."""
        result = []
        for bm in self.benchmarks.values():
            result.append(bm.info.model_dump())
        return result

    def get_benchmark(self, name: str) -> Benchmark | None:
        """Get a benchmark instance by name."""
        return self.benchmarks.get(name)

    def get_all_names(self) -> list[str]:
        """Return all registered benchmark names."""
        return list(self.benchmarks.keys())

# Singleton instance
registry = BenchmarkRegistry()
