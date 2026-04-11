"""
Tests for demo seed idempotency.
"""
from unittest.mock import MagicMock, patch

import pytest
from app.models_demo import DemoSeedLog
from app.scripts.demo_seed import seed_demo
from sqlalchemy.orm import Session


@pytest.fixture
def mock_db_session():
    mock_session = MagicMock(spec=Session)
    with patch('app.scripts.demo_seed.get_db', return_value=iter([mock_session])):
        yield mock_session

def test_seed_idempotent_first_run(mock_db_session):
    """Test first seed run creates data."""
    # No existing seed log
    mock_db_session.query.return_value.filter.return_value.first.return_value = None
    
    result = seed_demo(mock_db_session, force=False)
    
    assert result["seeded"] is True
    assert result["skipped"] is False
    assert "timing" in result
    mock_db_session.add.assert_called()
    mock_db_session.commit.assert_called()

def test_seed_idempotent_second_run_skips(mock_db_session):
    """Test second seed run is faster and doesn't duplicate."""
    # Existing completed seed log
    existing_log = DemoSeedLog(
        run_id="existing_run",
        summary={"status": "completed"}
    )
    mock_db_session.query.return_value.filter.return_value.first.return_value = existing_log
    
    result = seed_demo(mock_db_session, force=False)
    
    assert result["seeded"] is True
    assert result["skipped"] is True
    assert result["timing"]["total_ms"] == 0
    # Should not add new data
    mock_db_session.add.assert_not_called()

def test_seed_force_overrides_skip(mock_db_session):
    """Test force=True overrides skip logic."""
    # Existing completed seed log
    existing_log = DemoSeedLog(
        run_id="existing_run",
        summary={"status": "completed"}
    )
    mock_db_session.query.return_value.filter.return_value.first.return_value = existing_log
    
    result = seed_demo(mock_db_session, force=True)
    
    assert result["seeded"] is True
    assert result["skipped"] is False
    # Should proceed with seeding
    mock_db_session.add.assert_called()

def test_seed_counts_stable(mock_db_session):
    """Test that counts remain stable across runs."""
    # First run
    mock_db_session.query.return_value.filter.return_value.first.return_value = None
    result1 = seed_demo(mock_db_session, force=False)
    
    # Reset mocks for second run
    mock_db_session.reset_mock()
    mock_db_session.query.return_value.filter.return_value.first.return_value = None
    
    result2 = seed_demo(mock_db_session, force=True)
    
    # Counts should be the same
    assert result1["users"] == result2["users"]
    assert result1["merchants"] == result2["merchants"]
    assert result1["utilities"] == result2["utilities"]
