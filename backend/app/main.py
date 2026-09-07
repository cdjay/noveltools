from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import Base, engine, ensure_schema
from .routers.core import router
from .routers.outline import router as outline_router
from .routers.summary import router as summary_router
from .outline_history import migrate_legacy_outlines

Base.metadata.create_all(bind=engine)
ensure_schema()
migrate_legacy_outlines()

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
app.include_router(outline_router)
app.include_router(summary_router)
