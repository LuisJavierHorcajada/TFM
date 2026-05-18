"""
ESI-Bench - FastAPI deployment.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import database
from app.routers import benchmarks, results
from app.services.registry import registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.connect()
    registry.discover()
    print(f"Discovered {len(registry.benchmarks)} benchmark(s)")
    yield
    
    await database.disconnect()


app = FastAPI(
    title="ESI-Bench",
    description="Plugin system benchmark",
    version="1.0.0",
    lifespan=lifespan,
)

# Routers
app.include_router(benchmarks.router, prefix="/api/benchmarks", tags=["Benchmarks"])
app.include_router(results.router, prefix="/api/results", tags=["Results"])

# Static files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(str(static_dir / "index.html"))
