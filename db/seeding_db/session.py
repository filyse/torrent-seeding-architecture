from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from seeding_db.models import Base


def create_engine(url: str):
    return create_async_engine(url, echo=False)


def create_session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def lifespan_session(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


async def init_models(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
