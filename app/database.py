"""
ESI-Bench - MongoDB connection manager.
"""

import logging

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import settings

logger = logging.getLogger("esi_bench.database")


class Database:
    """Database connection manager."""

    client: AsyncIOMotorClient | None = None
    db: AsyncIOMotorDatabase | None = None

    async def connect(self) -> None:
        self.client = AsyncIOMotorClient(settings.MONGO_URL)
        self.db = self.client[settings.MONGO_DB]

        # Verify connection
        await self.client.admin.command("ping")
        logger.info("Connected to MongoDB at %s/%s", settings.MONGO_URL, settings.MONGO_DB)

    async def disconnect(self) -> None:
        if self.client:
            self.client.close()
            logger.info("Disconnected from MongoDB")

    def get_collection(self, name: str):
        """Get a collection by name."""
        if self.db is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self.db[name]


# Singleton instance
database = Database()
