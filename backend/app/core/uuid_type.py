"""
SQLite-compatible UUID type for SQLAlchemy.

This module provides a UUID type that works seamlessly with both SQLite and PostgreSQL.
For SQLite, UUIDs are stored as CHAR(36) strings. For PostgreSQL, native UUID type is used.
"""
import uuid

from sqlalchemy import String, TypeDecorator


class UUIDType(TypeDecorator):
    """
    SQLite-compatible UUID type.
    
    Stores UUIDs as CHAR(36) strings in SQLite and native UUID in PostgreSQL.
    Automatically handles conversion between Python UUID objects and database strings.
    
    Usage:
        id = Column(UUIDType(), primary_key=True)
    """
    impl = String
    cache_ok = True
    
    def load_dialect_impl(self, dialect):
        """Return the appropriate type for the database dialect.

        Always uses String(36) because production PostgreSQL columns were
        created as character varying, not native UUID.  Using
        postgresql.UUID here causes 'operator does not exist: character
        varying = uuid' errors on WHERE clauses.
        """
        return dialect.type_descriptor(String(36))
    
    def process_bind_param(self, value, dialect):
        """Convert Python value to database value."""
        if value is None:
            return None
        
        # If already a string, return as-is (for SQLite compatibility)
        if isinstance(value, str):
            # Validate it's a valid UUID string
            try:
                uuid.UUID(value)
                return value
            except ValueError:
                raise ValueError(f"Invalid UUID string: {value}")
        
        # If UUID object, convert to string
        if isinstance(value, uuid.UUID):
            return str(value)
        
        # Try to convert to UUID
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, AttributeError):
            raise ValueError(f"Cannot convert {value} to UUID")
    
    def process_result_value(self, value, dialect):
        """Convert database value to Python value."""
        if value is None:
            return None
        
        # PostgreSQL returns UUID objects, convert to string
        if isinstance(value, uuid.UUID):
            return str(value)
        
        # SQLite returns strings, validate and return
        if isinstance(value, str):
            try:
                # Validate format
                uuid.UUID(value)
                return value
            except ValueError:
                raise ValueError(f"Invalid UUID string from database: {value}")
        
        return str(value)


# Convenience alias for backward compatibility
UUID = UUIDType







