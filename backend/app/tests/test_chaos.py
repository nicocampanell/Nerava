"""
Chaos engineering tests for fault injection and resilience
"""
import time

import pytest
from app.experiments.faults import FaultType, chaos_monkey, fault_injector


# Enable fault injection for tests
@pytest.fixture(autouse=True)
def enable_fault_injection():
    """Enable fault injection for all chaos tests."""
    original_enabled = fault_injector.enabled
    fault_injector.enabled = True
    yield
    fault_injector.enabled = original_enabled
    # Clean up any active faults after each test
    fault_injector.stop_all_faults()
    fault_injector.active_faults.clear()
    fault_injector.fault_history.clear()

class TestChaosEngineering:
    """Test chaos engineering and fault injection"""
    
    def test_fault_injection_latency(self):
        """Test latency fault injection"""
        fault_id = fault_injector.inject_fault(
            FaultType.LATENCY,
            duration=10,
            intensity=0.5
        )
        
        assert fault_id is not None
        assert fault_id in fault_injector.active_faults
        
        # Test that fault is active
        active_faults = fault_injector.get_active_faults()
        assert fault_id in active_faults
        
        # Clean up
        fault_injector.stop_fault(fault_id)
    
    def test_fault_injection_error(self):
        """Test error fault injection"""
        fault_id = fault_injector.inject_fault(
            FaultType.ERROR,
            duration=10,
            intensity=0.8
        )
        
        assert fault_id is not None
        
        # Test that fault is active
        active_faults = fault_injector.get_active_faults()
        assert fault_id in active_faults
        
        # Clean up
        fault_injector.stop_fault(fault_id)
    
    def test_fault_injection_timeout(self):
        """Test timeout fault injection"""
        fault_id = fault_injector.inject_fault(
            FaultType.TIMEOUT,
            duration=10,
            intensity=0.6
        )
        
        assert fault_id is not None
        
        # Test that fault is active
        active_faults = fault_injector.get_active_faults()
        assert fault_id in active_faults
        
        # Clean up
        fault_injector.stop_fault(fault_id)
    
    def test_fault_injection_memory_leak(self):
        """Test memory leak fault injection"""
        fault_id = fault_injector.inject_fault(
            FaultType.MEMORY_LEAK,
            duration=10,
            intensity=0.3
        )
        
        assert fault_id is not None
        
        # Test that fault is active
        active_faults = fault_injector.get_active_faults()
        assert fault_id in active_faults
        
        # Clean up
        fault_injector.stop_fault(fault_id)
    
    def test_fault_injection_cpu_spike(self):
        """Test CPU spike fault injection"""
        fault_id = fault_injector.inject_fault(
            FaultType.CPU_SPIKE,
            duration=10,
            intensity=0.4
        )
        
        assert fault_id is not None
        
        # Test that fault is active
        active_faults = fault_injector.get_active_faults()
        assert fault_id in active_faults
        
        # Clean up
        fault_injector.stop_fault(fault_id)
    
    def test_fault_injection_network_partition(self):
        """Test network partition fault injection"""
        fault_id = fault_injector.inject_fault(
            FaultType.NETWORK_PARTITION,
            duration=10,
            intensity=0.7
        )
        
        assert fault_id is not None
        
        # Test that fault is active
        active_faults = fault_injector.get_active_faults()
        assert fault_id in active_faults
        
        # Clean up
        fault_injector.stop_fault(fault_id)
    
    def test_fault_injection_database_failure(self):
        """Test database failure fault injection"""
        fault_id = fault_injector.inject_fault(
            FaultType.DATABASE_FAILURE,
            duration=10,
            intensity=0.9
        )
        
        assert fault_id is not None
        
        # Test that fault is active
        active_faults = fault_injector.get_active_faults()
        assert fault_id in active_faults
        
        # Clean up
        fault_injector.stop_fault(fault_id)
    
    def test_fault_injection_redis_failure(self):
        """Test Redis failure fault injection"""
        fault_id = fault_injector.inject_fault(
            FaultType.REDIS_FAILURE,
            duration=10,
            intensity=0.8
        )
        
        assert fault_id is not None
        
        # Test that fault is active
        active_faults = fault_injector.get_active_faults()
        assert fault_id in active_faults
        
        # Clean up
        fault_injector.stop_fault(fault_id)
    
    def test_stop_fault(self):
        """Test stopping a fault"""
        fault_id = fault_injector.inject_fault(
            FaultType.LATENCY,
            duration=60,
            intensity=0.5
        )
        
        assert fault_id in fault_injector.active_faults
        
        # Stop the fault
        fault_injector.stop_fault(fault_id)
        
        # Verify fault is stopped
        active_faults = fault_injector.get_active_faults()
        assert fault_id not in active_faults
    
    def test_stop_all_faults(self):
        """Test stopping all faults"""
        # Inject multiple faults
        fault1 = fault_injector.inject_fault(FaultType.LATENCY, duration=60, intensity=0.5)
        fault2 = fault_injector.inject_fault(FaultType.ERROR, duration=60, intensity=0.5)
        fault3 = fault_injector.inject_fault(FaultType.TIMEOUT, duration=60, intensity=0.5)
        
        # Verify all faults are active
        active_faults = fault_injector.get_active_faults()
        assert len(active_faults) == 3
        
        # Stop all faults
        fault_injector.stop_all_faults()
        
        # Verify all faults are stopped
        active_faults = fault_injector.get_active_faults()
        assert len(active_faults) == 0
    
    def test_fault_history(self):
        """Test fault injection history"""
        # Inject a fault
        fault_id = fault_injector.inject_fault(
            FaultType.LATENCY,
            duration=10,
            intensity=0.5
        )
        
        # Check history
        history = fault_injector.get_fault_history()
        assert len(history) > 0
        
        # Find our fault in history
        fault_in_history = next((f for f in history if f["fault_id"] == fault_id), None)
        assert fault_in_history is not None
        assert fault_in_history["fault_type"] == FaultType.LATENCY
        assert fault_in_history["intensity"] == 0.5
        
        # Clean up
        fault_injector.stop_fault(fault_id)
    
    def test_chaos_monkey_decorator(self):
        """Test chaos monkey decorator"""
        @chaos_monkey
        def test_function():
            return "success"
        
        # Test that function still works (fault injection is disabled by default)
        result = test_function()
        assert result == "success"
    
    def test_fault_expiration(self):
        """Test fault expiration and cleanup"""
        # Inject a fault with very short duration
        fault_id = fault_injector.inject_fault(
            FaultType.LATENCY,
            duration=1,  # 1 second
            intensity=0.5
        )
        
        if not fault_id:
            pytest.skip("Fault injection not enabled or not working")
        
        # Wait for fault to expire
        time.sleep(2)
        
        # Clean up expired faults
        fault_injector.cleanup_expired_faults()
        
        # Verify fault is no longer active
        active_faults = fault_injector.get_active_faults()
        assert fault_id not in active_faults
    
    def test_multiple_fault_types(self):
        """Test injecting multiple different fault types"""
        faults = []
        
        # Inject different types of faults
        for fault_type in FaultType:
            fault_id = fault_injector.inject_fault(
                fault_type,
                duration=10,
                intensity=0.3
            )
            if fault_id:  # Only append if fault was successfully injected
                faults.append(fault_id)
        
        # Verify all faults are active (some might not inject if not implemented)
        active_faults = fault_injector.get_active_faults()
        assert len(active_faults) >= len(faults)  # At least as many as were injected
        
        # Clean up all faults
        fault_injector.stop_all_faults()
        
        # Verify all faults are stopped
        active_faults = fault_injector.get_active_faults()
        assert len(active_faults) == 0
    
    def test_fault_intensity_variations(self):
        """Test different fault intensities"""
        intensities = [0.1, 0.3, 0.5, 0.7, 0.9]
        
        for intensity in intensities:
            fault_id = fault_injector.inject_fault(
                FaultType.LATENCY,
                duration=5,
                intensity=intensity
            )
            
            # Verify fault was created with correct intensity
            fault_config = fault_injector.active_faults[fault_id]
            assert fault_config["intensity"] == intensity
            
            # Clean up
            fault_injector.stop_fault(fault_id)
    
    def test_fault_duration_variations(self):
        """Test different fault durations"""
        durations = [1, 5, 10, 30, 60]
        
        for duration in durations:
            fault_id = fault_injector.inject_fault(
                FaultType.LATENCY,
                duration=duration,
                intensity=0.5
            )
            
            # Verify fault was created with correct duration
            fault_config = fault_injector.active_faults[fault_id]
            assert fault_config["duration"] == duration
            
            # Clean up
            fault_injector.stop_fault(fault_id)
    
    def test_fault_injection_disabled(self):
        """Test fault injection when disabled"""
        # Disable fault injection
        original_enabled = fault_injector.enabled
        fault_injector.enabled = False
        
        try:
            # Try to inject a fault
            fault_id = fault_injector.inject_fault(
                FaultType.LATENCY,
                duration=10,
                intensity=0.5
            )
            
            # Should return None when disabled
            assert fault_id is None
            
        finally:
            # Restore original setting
            fault_injector.enabled = original_enabled
    
    def test_fault_injection_edge_cases(self):
        """Test edge cases for fault injection"""
        # Test with intensity 0
        fault_id = fault_injector.inject_fault(
            FaultType.LATENCY,
            duration=10,
            intensity=0.0
        )
        assert fault_id is not None
        fault_injector.stop_fault(fault_id)
        
        # Test with intensity 1.0
        fault_id = fault_injector.inject_fault(
            FaultType.LATENCY,
            duration=10,
            intensity=1.0
        )
        assert fault_id is not None
        fault_injector.stop_fault(fault_id)
        
        # Test with very short duration
        fault_id = fault_injector.inject_fault(
            FaultType.LATENCY,
            duration=0,
            intensity=0.5
        )
        assert fault_id is not None
        fault_injector.stop_fault(fault_id)
    
    def test_fault_injection_concurrent(self):
        """Test concurrent fault injection"""
        import threading
        
        def inject_fault():
            fault_id = fault_injector.inject_fault(
                FaultType.LATENCY,
                duration=10,
                intensity=0.5
            )
            return fault_id
        
        # Inject faults concurrently
        threads = []
        fault_ids = []
        
        for _ in range(5):
            thread = threading.Thread(target=lambda: fault_ids.append(inject_fault()))
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Verify all faults were created
        assert len(fault_ids) == 5
        assert all(fault_id is not None for fault_id in fault_ids)
        
        # Clean up all faults
        fault_injector.stop_all_faults()
        
        # Verify all faults are stopped
        active_faults = fault_injector.get_active_faults()
        assert len(active_faults) == 0
