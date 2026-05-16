"""
Database module for Fantasy Baseball Assistant.
Sets up SQLite database and provides session management.
"""

import logging
import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker, declarative_base
from sqlalchemy.engine import Engine

from app.config import get_settings

logger = logging.getLogger(__name__)

Base = declarative_base()


def get_db_path() -> Path:
    """Get the database path from settings."""
    settings = get_settings()
    db_url = settings.DATABASE_URL
    
    # Parse SQLite URL
    if db_url.startswith("sqlite:///"):
        db_path = db_url.replace("sqlite:///", "")
        if db_path.startswith("./"):
            db_path = db_path[2:]
    else:
        db_path = "./data/fantasy_assistant.db"
    
    return Path(db_path)


def get_db_url() -> str:
    """Get the full database URL."""
    settings = get_settings()
    return settings.DATABASE_URL


def ensure_data_dir() -> None:
    """Ensure the data directory exists."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)


def get_engine() -> Engine:
    """Create and configure the database engine."""
    ensure_data_dir()
    db_url = get_db_url()
    
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False} if "sqlite" in db_url else {},
        echo=False  # Set to True for SQL debugging
    )
    
    # Enable foreign key constraints for SQLite
    if "sqlite" in db_url:
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    
    return engine


# Global engine and session factory
_engine = None
_SessionLocal = None


def get_db_engine() -> Engine:
    """Get or create the global engine instance."""
    global _engine
    if _engine is None:
        _engine = get_engine()
    return _engine


def get_session_factory() -> sessionmaker:
    """Get or create the session factory."""
    global _SessionLocal
    if _SessionLocal is None:
        engine = get_db_engine()
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """
    Dependency for getting database sessions.
    Yields a session and ensures it's closed after use.
    """
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Initialize the database by creating all tables."""
    engine = get_db_engine()
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized successfully")


def reset_db() -> None:
    """Reset the database by dropping and recreating all tables."""
    engine = get_db_engine()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    logger.info("Database reset completed")