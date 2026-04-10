"""
Tests for observability features including logging and metrics.
"""
from unittest.mock import Mock, patch

from app.main_simple import app
from fastapi.testclient import TestClient

client = TestClient(app)

class TestObservability:
    """Test observability features."""
    
    def test_trace_id_generation(self):
        """Test that trace IDs are generated for requests."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.obs.obs.get_trace_id') as mock_trace_id:
                mock_trace_id.return_value = "test-trace-123"
                
                with patch('app.security.scopes.require_scopes') as mock_scopes:
                    mock_scopes.return_value = Mock(return_value={"user_id": "test_user"})
                    
                    response = client.get("/v1/merchant/intel/overview?merchant_id=M123")
                    
                    # Verify trace ID was generated
                    mock_trace_id.assert_called()
    
    def test_structured_logging(self):
        """Test that structured logging is called with correct parameters."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.obs.obs.log_info') as mock_log_info:
                with patch('app.obs.obs.log_error') as mock_log_error:
                    with patch('app.security.scopes.require_scopes') as mock_scopes:
                        mock_scopes.return_value = Mock(return_value={"user_id": "test_user"})
                        
                        response = client.get("/v1/merchant/intel/overview?merchant_id=M123")
                        
                        # Verify log_info was called
                        mock_log_info.assert_called()
                        
                        # Check that log_info was called with expected structure
                        call_args = mock_log_info.call_args[0][0]
                        assert "trace_id" in call_args
                        assert "route" in call_args
                        assert "merchant_id" in call_args
                        assert "actor_id" in call_args
    
    def test_metrics_counters_increment(self):
        """Test that metrics counters are incremented."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.obs.obs.api_requests_total') as mock_counter:
                with patch('app.security.scopes.require_scopes') as mock_scopes:
                    mock_scopes.return_value = Mock(return_value={"user_id": "test_user"})
                    
                    response = client.get("/v1/merchant/intel/overview?merchant_id=M123")
                    
                    # Verify counter was incremented
                    mock_counter.assert_called()
    
    def test_metrics_timers_record(self):
        """Test that metrics timers are recorded."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.obs.obs.api_request_ms') as mock_timer:
                with patch('app.security.scopes.require_scopes') as mock_scopes:
                    mock_scopes.return_value = Mock(return_value={"user_id": "test_user"})
                    
                    response = client.get("/v1/merchant/intel/overview?merchant_id=M123")
                    
                    # Verify timer was recorded
                    mock_timer.assert_called()
    
    def test_error_logging(self):
        """Test that errors are logged with structured format."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.obs.obs.log_error') as mock_log_error:
                with patch('app.security.scopes.require_scopes') as mock_scopes:
                    # Mock an exception
                    mock_scopes.side_effect = Exception("Test error")
                    
                    response = client.get("/v1/merchant/intel/overview?merchant_id=M123")
                    
                    # Verify log_error was called
                    mock_log_error.assert_called()
                    
                    # Check that log_error was called with expected structure
                    call_args = mock_log_error.call_args[0][0]
                    assert "trace_id" in call_args
                    assert "route" in call_args
                    assert "error" in call_args
    
    def test_success_logging(self):
        """Test that successful requests are logged."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.obs.obs.log_info') as mock_log_info:
                with patch('app.security.scopes.require_scopes') as mock_scopes:
                    mock_scopes.return_value = Mock(return_value={"user_id": "test_user"})
                    
                    response = client.get("/v1/merchant/intel/overview?merchant_id=M123")
                    
                    # Verify success logging
                    mock_log_info.assert_called()
                    
                    # Check that success log includes status
                    success_calls = [call for call in mock_log_info.call_args_list 
                                   if len(call[0]) > 0 and "status" in call[0][0]]
                    assert len(success_calls) > 0
    
    def test_middleware_trace_id_setting(self):
        """Test that middleware sets trace ID in request state."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.middleware.metrics.MetricsMiddleware') as mock_middleware:
                with patch('app.security.scopes.require_scopes') as mock_scopes:
                    mock_scopes.return_value = Mock(return_value={"user_id": "test_user"})
                    
                    response = client.get("/v1/merchant/intel/overview?merchant_id=M123")
                    
                    # Verify middleware was called
                    mock_middleware.assert_called()
    
    def test_metrics_per_route(self):
        """Test that metrics are recorded per route."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.obs.obs.api_requests_total') as mock_counter:
                with patch('app.security.scopes.require_scopes') as mock_scopes:
                    mock_scopes.return_value = Mock(return_value={"user_id": "test_user"})
                    
                    # Test multiple routes
                    client.get("/v1/merchant/intel/overview?merchant_id=M123")
                    client.get("/v1/utility/behavior/cloud?utility_id=U123&window=24h")
                    
                    # Verify counter was called for each route
                    assert mock_counter.call_count >= 2
    
    def test_trace_id_consistency(self):
        """Test that trace ID is consistent throughout request lifecycle."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.obs.obs.get_trace_id') as mock_trace_id:
                mock_trace_id.return_value = "consistent-trace-123"
                
                with patch('app.security.scopes.require_scopes') as mock_scopes:
                    mock_scopes.return_value = Mock(return_value={"user_id": "test_user"})
                    
                    response = client.get("/v1/merchant/intel/overview?merchant_id=M123")
                    
                    # Verify trace ID was called consistently
                    mock_trace_id.assert_called()
                    
                    # Check that all log calls use the same trace ID
                    with patch('app.obs.obs.log_info') as mock_log_info:
                        mock_log_info.assert_called()
                        # All calls should use the same trace ID
                        for call_args in mock_log_info.call_args_list:
                            if len(call_args[0]) > 0:
                                assert call_args[0][0]["trace_id"] == "consistent-trace-123"
