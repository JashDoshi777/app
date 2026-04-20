"""
Database connection manager for NeonDB (PostgreSQL).
Uses SQLAlchemy async engine with asyncpg driver.
"""

import ssl
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
import config

_db_url = config.DATABASE_URL

if _db_url:
    # Switch to asyncpg driver
    if _db_url.startswith("postgresql://"):
        _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql+asyncpg://", 1)

    # asyncpg doesn't support sslmode/channel_binding as query params
    # Strip them and configure SSL separately
    parsed = urlparse(_db_url)
    query_params = parse_qs(parsed.query)
    needs_ssl = query_params.pop("sslmode", [None])[0] in ("require", "verify-ca", "verify-full")
    query_params.pop("channel_binding", None)  # asyncpg doesn't support this either

    # Rebuild URL without sslmode/channel_binding
    clean_query = urlencode({k: v[0] for k, v in query_params.items()})
    _db_url = urlunparse(parsed._replace(query=clean_query))

    # Create SSL context for NeonDB
    connect_args = {}
    if needs_ssl:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        connect_args["ssl"] = ssl_ctx

    engine = create_async_engine(
        _db_url,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
else:
    engine = None

async_session = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
) if engine else None


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


async def init_db():
    """Create all tables if they don't exist."""
    if engine is None:
        print("[DB] WARNING: No DATABASE_URL configured — running without persistence.")
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[DB] Tables initialised successfully.")


async def get_session() -> AsyncSession:
    """Yield an async database session."""
    if async_session is None:
        raise RuntimeError("Database not configured. Set DATABASE_URL in .env")
    async with async_session() as session:
        yield session
