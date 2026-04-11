"""
Tests for demo state management.
"""
from unittest.mock import MagicMock, patch

import pytest
from app.models_demo import DemoState
from app.routers.demo import set_scenario
from sqlalchemy.orm import Session


@pytest.fixture
def mock_db_session():
    mock_session = MagicMock(spec=Session)
    with patch('app.routers.demo.get_db', return_value=iter([mock_session])):
        yield mock_session

def test_set_scenario_new_key(mock_db_session):
    """Test setting a new scenario key."""
    mock_db_session.query.return_value.filter.return_value.first.return_value = None
    
    result = set_scenario("grid_state", "peak")
    
    assert result["ok"] is True
    assert result["state"]["grid_state"] == "peak"
    mock_db_session.add.assert_called_once()
    mock_db_session.commit.assert_called()

def test_set_scenario_existing_key(mock_db_session):
    """Test updating an existing scenario key."""
    existing_state = DemoState(key="grid_state", value="offpeak")
    mock_db_session.query.return_value.filter.return_value.first.return_value = existing_state
    
    result = set_scenario("grid_state", "peak")
    
    assert result["ok"] is True
    assert result["state"]["grid_state"] == "peak"
    assert existing_state.value == "peak"
    mock_db_session.commit.assert_called()

