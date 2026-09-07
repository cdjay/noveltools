from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    if settings.database_url.startswith("sqlite:///"):
        database_path = Path(settings.database_url.removeprefix("sqlite:///"))
        if not database_path.is_absolute():
            database_path = Path.cwd() / database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
    return settings.database_url


engine = create_engine(_database_url(), connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def enable_foreign_keys(connection, _record):
    # SQLite otherwise ignores ON DELETE CASCADE and leaves versions behind.
    connection.execute("PRAGMA foreign_keys=ON")


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def ensure_schema() -> None:
    """Apply the small additive migrations needed by the local SQLite app."""
    version_columns = {column["name"] for column in inspect(engine).get_columns("chapter_versions")}
    if "content_text" not in version_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE chapter_versions ADD COLUMN content_text TEXT"))
    chapter_columns = {column["name"] for column in inspect(engine).get_columns("chapters")}
    chapter_statements = []
    for name, definition in {
        "summary": "TEXT NOT NULL DEFAULT ''",
        "summary_content_sha256": "VARCHAR(64) NOT NULL DEFAULT ''",
        "summary_source_version_id": "INTEGER",
        "summary_model": "VARCHAR(120) NOT NULL DEFAULT ''",
        "summary_updated_at": "DATETIME",
    }.items():
        if name not in chapter_columns:
            chapter_statements.append(f"ALTER TABLE chapters ADD COLUMN {name} {definition}")
    character_columns = {column["name"] for column in inspect(engine).get_columns("characters")}
    character_statements = []
    for name, definition in {
        "summary": "TEXT NOT NULL DEFAULT ''",
        "appearance": "TEXT NOT NULL DEFAULT ''",
        "level": "VARCHAR(120) NOT NULL DEFAULT ''",
        "inventory": "TEXT NOT NULL DEFAULT ''",
        "relationships": "TEXT NOT NULL DEFAULT ''",
    }.items():
        if name not in character_columns:
            character_statements.append(f"ALTER TABLE characters ADD COLUMN {name} {definition}")
    columns = {column["name"] for column in inspect(engine).get_columns("llm_providers")}
    statements = []
    if "provider_type" not in columns:
        statements.append("ALTER TABLE llm_providers ADD COLUMN provider_type VARCHAR(40) NOT NULL DEFAULT 'openai_compatible'")
    if "conversation_id" not in columns:
        statements.append("ALTER TABLE llm_providers ADD COLUMN conversation_id VARCHAR(300) NOT NULL DEFAULT ''")
    graphify_columns = {column["name"] for column in inspect(engine).get_columns("graphify_runs")}
    graphify_statements = []
    for name, definition in {
        "source_chapter_id": "INTEGER",
        "source_version_id": "INTEGER",
        "content_sha256": "VARCHAR(64) NOT NULL DEFAULT ''",
    }.items():
        if name not in graphify_columns:
            graphify_statements.append(f"ALTER TABLE graphify_runs ADD COLUMN {name} {definition}")
    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
    if graphify_statements:
        with engine.begin() as connection:
            for statement in graphify_statements:
                connection.execute(text(statement))
    if character_statements:
        with engine.begin() as connection:
            for statement in character_statements:
                connection.execute(text(statement))
    if chapter_statements:
        with engine.begin() as connection:
            for statement in chapter_statements:
                connection.execute(text(statement))
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
