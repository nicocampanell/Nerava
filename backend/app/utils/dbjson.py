"""
Database JSON handling utilities (dialect-aware)
"""
import json
from typing import Any, Dict

from app.config import settings
from sqlalchemy import Engine, inspect


def as_db_json(value: Dict[str, Any], engine: Engine = None) -> Any:
    """
    Convert a dict to DB-appropriate JSON format.
    
    - SQLite: Returns JSON string (json.dumps) for TEXT columns
    - Postgres: Returns dict directly for JSON/JSONB columns
    """
    if engine is None:
        # Infer from database_url
        is_sqlite = settings.database_url.startswith("sqlite")
        if is_sqlite:
            return json.dumps(value)
        return value
    
    # Use engine dialect
    dialect_name = engine.dialect.name
    if dialect_name == 'sqlite':
        return json.dumps(value)
    else:
        # Postgres/other - return dict (SQLAlchemy handles JSON/JSONB)
        return value


def get_table_columns(engine: Engine, table_name: str) -> Dict[str, str]:
    """
    Get column names and types for a table.
    Returns dict mapping column_name -> column_type
    """
    inspector = inspect(engine)
    columns = {}
    for col in inspector.get_columns(table_name):
        columns[col['name']] = str(col['type'])
    return columns

