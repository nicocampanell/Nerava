"""
Database routing for read/write separation
"""

from typing import Optional

from app.config import settings
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool


class DatabaseRouter:
    """Router for managing primary and read replica databases"""

    def __init__(self):
        self.primary_engine = create_engine(
            settings.database_url,
            poolclass=QueuePool,
            pool_size=20,
            max_overflow=30,
            pool_pre_ping=True,
            pool_recycle=3600,
            connect_args=(
                {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
            ),
        )

        # Read replica engine (if configured)
        self.read_engine: Optional[Engine] = None
        if settings.read_database_url:
            self.read_engine = create_engine(
                settings.read_database_url,
                poolclass=QueuePool,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
                pool_recycle=3600,
                connect_args=(
                    {"check_same_thread": False}
                    if settings.read_database_url.startswith("sqlite")
                    else {}
                ),
            )

        # Session makers
        self.primary_session = sessionmaker(
            autocommit=False, autoflush=False, bind=self.primary_engine
        )
        self.read_session = (
            sessionmaker(autocommit=False, autoflush=False, bind=self.read_engine)
            if self.read_engine
            else None
        )

    def get_session(self, use_primary: bool = True) -> Session:
        """Get a database session for the appropriate database"""
        if use_primary or not self.read_session:
            return self.primary_session()
        else:
            return self.read_session()

    def get_engine(self, use_primary: bool = True) -> Engine:
        """Get a database engine for the appropriate database"""
        if use_primary or not self.read_engine:
            return self.primary_engine
        else:
            return self.read_engine

    def health_check(self) -> dict:
        """Check health of both databases"""
        health = {
            "primary": {"healthy": False, "error": None},
            "read_replica": {"healthy": False, "error": None},
        }

        # Check primary database
        try:
            with self.primary_engine.connect() as conn:
                conn.execute("SELECT 1")
            health["primary"]["healthy"] = True
        except Exception as e:
            health["primary"]["error"] = str(e)

        # Check read replica (if configured)
        if self.read_engine:
            try:
                with self.read_engine.connect() as conn:
                    conn.execute("SELECT 1")
                health["read_replica"]["healthy"] = True
            except Exception as e:
                health["read_replica"]["error"] = str(e)
        else:
            health["read_replica"]["healthy"] = True  # No read replica configured

        return health


# Global database router
db_router = DatabaseRouter()


def get_db_session(use_primary: bool = True):
    """Dependency for getting database session"""
    session = db_router.get_session(use_primary)
    try:
        yield session
    finally:
        session.close()


def get_db_engine(use_primary: bool = True):
    """Dependency for getting database engine"""
    return db_router.get_engine(use_primary)
