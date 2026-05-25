"""Pytest configuration and shared fixtures."""
import pytest
import pytest_asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from scraper.db.models import Base

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def test_db():
    """Create in-memory SQLite test database."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        future=True,
    )
    
    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with SessionLocal() as session:
        yield session
    
    # Cleanup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    
    await engine.dispose()


@pytest.fixture
def mock_httpx_client():
    """Mock httpx client for testing."""
    import httpx
    from unittest.mock import AsyncMock, MagicMock
    
    client = MagicMock(spec=httpx.AsyncClient)
    client.get = AsyncMock()
    return client


@pytest.mark.unit
class TestMarkerUnit:
    """Marker for unit tests."""
    pass


@pytest.mark.integration
class TestMarkerIntegration:
    """Marker for integration tests."""
    pass
