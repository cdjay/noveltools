import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import Base, engine, ensure_schema
from .routers.core import auto_graphify_existing_chapters, router

Base.metadata.create_all(bind=engine)
ensure_schema()

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.on_event("startup")
async def reconcile_graphify_on_startup() -> None:
    # Existing chapters are reconciled in the background. New writes use the
    # same worker directly, so both paths share SHA deduplication and logging.
    asyncio.create_task(auto_graphify_existing_chapters())
