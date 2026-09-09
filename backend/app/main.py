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
from .routers.materials import router as materials_router
from .routers.llm_routes import router as llm_routes_router
from .routers.llm_provider_actions import router as llm_provider_actions_router
from .routers import writing_jobs
from .routers.chapter_plans import router as chapter_plans_router
from .routers import canvas_writing

Base.metadata.create_all(bind=engine)
ensure_schema()
migrate_legacy_outlines()

@asynccontextmanager
async def lifespan(app):
    recover_interrupted()
    writing_jobs.recover_interrupted()
    canvas_writing.recover_interrupted()
    task = asyncio.create_task(worker())
    writing_task = asyncio.create_task(writing_jobs.worker())
    canvas_task = asyncio.create_task(canvas_writing.worker())
    yield
    canvas_task.cancel()
    with suppress(asyncio.CancelledError):
        await canvas_task
    writing_task.cancel()
    task.cancel()
    with suppress(asyncio.CancelledError):
        await writing_task
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"], expose_headers=["X-State-Version", "X-State-Stale", "X-Material-Version"])
app.include_router(router)
app.include_router(outline_router)
app.include_router(summary_router)
app.include_router(jobs_router)
app.include_router(backups_router)
app.include_router(current_state_router)
app.include_router(materials_router)
app.include_router(llm_routes_router)
app.include_router(llm_provider_actions_router)
app.include_router(writing_jobs.router)
app.include_router(chapter_plans_router)
app.include_router(canvas_writing.router)
