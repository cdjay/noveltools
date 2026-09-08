from fastapi import FastAPI
import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import Base, engine, ensure_schema
from .routers.core import router
from .routers.outline import router as outline_router
from .routers.summary import router as summary_router
from .outline_history import migrate_legacy_outlines
from .routers.summary_jobs import router as jobs_router, worker, recover_interrupted
from .routers.backups import router as backups_router
from .routers.current_state import router as current_state_router

Base.metadata.create_all(bind=engine)
ensure_schema()
migrate_legacy_outlines()

@asynccontextmanager
async def lifespan(app):
    recover_interrupted()
    task = asyncio.create_task(worker())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"], expose_headers=["X-State-Version", "X-State-Stale"])
app.include_router(router)
app.include_router(outline_router)
app.include_router(summary_router)
app.include_router(jobs_router)
app.include_router(backups_router)
app.include_router(current_state_router)
