"""
PostgreSQL Database Connection and Pool Manager for MCP SmartCache.
Provides robust connection pooling via asyncpg with explicit timeouts,
connection recycling, health validation, and transaction management.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
import asyncpg

logger = logging.getLogger(__name__)

DEFAULT_DATABASE_URL = "postgresql://mcp_user:mcp_password@localhost:5432/mcp_smartcache"


def normalize_dsn(url: str) -> str:
    """Normalize SQLAlchemy-style postgresql+asyncpg DSN to standard postgresql DSN."""
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return url


import json

async def _init_connection(conn: asyncpg.Connection) -> None:
    """Register json/jsonb codecs for transparent serialization."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


class DatabaseManager:
    """
    Manages an asyncpg connection pool with health probes, bounded timeouts,
    and safe query execution.
    """

    def __init__(
        self,
        dsn: Optional[str] = None,
        min_size: int = 2,
        max_size: int = 10,
        timeout: float = 10.0,
        command_timeout: float = 15.0,
    ):
        raw_dsn = dsn or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
        self.dsn = normalize_dsn(raw_dsn)
        self.min_size = min_size
        self.max_size = max_size
        self.timeout = timeout
        self.command_timeout = command_timeout
        self.pool: Optional[asyncpg.Pool] = None

    def _is_pool_healthy(self) -> bool:
        if self.pool is None:
            return False
        if hasattr(self.pool, "_loop") and (self.pool._loop is None or self.pool._loop.is_closed()):
            return False
        return True

    async def connect(self) -> None:
        """Establish the connection pool if not already connected on the current event loop."""
        if self._is_pool_healthy():
            return
        self.pool = None
        try:
            self.pool = await asyncpg.create_pool(
                dsn=self.dsn,
                min_size=self.min_size,
                max_size=self.max_size,
                timeout=self.timeout,
                command_timeout=self.command_timeout,
                init=_init_connection,
            )
            logger.info("PostgreSQL connection pool established (min=%d, max=%d)", self.min_size, self.max_size)
        except Exception as err:
            logger.error("Failed to connect to PostgreSQL at %s: %s", self.dsn, err)
            raise ConnectionError(f"Database connection failed: {err}") from err

    async def disconnect(self) -> None:
        """Close all connections in the pool."""
        if self.pool is not None:
            try:
                if hasattr(self.pool, "_loop") and not self.pool._loop.is_closed():
                    await self.pool.close()
            except Exception:
                pass
            self.pool = None
            logger.info("PostgreSQL connection pool closed.")

    async def check_health(self) -> bool:
        """Liveness/readiness probe executing SELECT 1 with 3.0s timeout."""
        if not self._is_pool_healthy():
            try:
                await self.connect()
            except Exception:
                return False
        try:
            async with self.pool.acquire(timeout=3.0) as conn:
                val = await conn.fetchval("SELECT 1")
                return val == 1
        except Exception as err:
            logger.warning("Database health probe failed: %s", err)
            return False

    @asynccontextmanager
    async def acquire(self):
        """Acquire a connection from the pool."""
        if not self._is_pool_healthy():
            await self.connect()
        async with self.pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self):
        """Acquire a connection and begin an ACID transaction block."""
        if not self._is_pool_healthy():
            await self.connect()
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    async def execute(self, query: str, *args) -> str:
        """Execute a query returning the status string."""
        async with self.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args) -> List[asyncpg.Record]:
        """Execute a query returning all record rows."""
        async with self.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args) -> Optional[asyncpg.Record]:
        """Execute a query returning a single record row or None."""
        async with self.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args) -> Any:
        """Execute a query returning a scalar value."""
        async with self.acquire() as conn:
            return await conn.fetchval(query, *args)


# Global default database instance
db: DatabaseManager = DatabaseManager()
