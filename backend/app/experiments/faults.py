"""
Fault injection for chaos engineering and resilience testing
"""
import logging
import random
import time
from enum import Enum
from typing import Any, Dict, List

from app.config import settings

logger = logging.getLogger(__name__)

class FaultType(Enum):
    """Types of faults that can be injected"""
    LATENCY = "latency"
    ERROR = "error"
    TIMEOUT = "timeout"
    MEMORY_LEAK = "memory_leak"
    CPU_SPIKE = "cpu_spike"
    NETWORK_PARTITION = "network_partition"
    DATABASE_FAILURE = "database_failure"
    REDIS_FAILURE = "redis_failure"

class FaultInjector:
    """Fault injection system for chaos engineering"""
    
    def __init__(self):
        self.active_faults: Dict[str, Dict[str, Any]] = {}
        self.fault_history: List[Dict[str, Any]] = []
        self.enabled = getattr(settings, 'enable_fault_injection', False)
    
    def inject_fault(self, fault_type: FaultType, duration: int = 60, intensity: float = 0.5, **kwargs) -> str:
        """Inject a fault into the system"""
        if not self.enabled:
            logger.warning("Fault injection is disabled")
            return None
        
        fault_id = f"{fault_type.value}_{int(time.time())}"
        fault_config = {
            "fault_type": fault_type,
            "duration": duration,
            "intensity": intensity,
            "start_time": time.time(),
            "kwargs": kwargs,
            "active": True
        }
        
        self.active_faults[fault_id] = fault_config
        self.fault_history.append({
            **fault_config,
            "fault_id": fault_id,
            "status": "injected"
        })
        
        logger.warning(f"Injecting fault: {fault_id} - {fault_type.value} for {duration}s with intensity {intensity}")
        
        # Start fault execution
        self._execute_fault(fault_id, fault_config)
        
        return fault_id
    
    def _execute_fault(self, fault_id: str, fault_config: Dict[str, Any]):
        """Execute a specific fault"""
        fault_type = fault_config["fault_type"]
        duration = fault_config["duration"]
        intensity = fault_config["intensity"]
        
        if fault_type == FaultType.LATENCY:
            self._inject_latency(fault_id, duration, intensity)
        elif fault_type == FaultType.ERROR:
            self._inject_error(fault_id, duration, intensity)
        elif fault_type == FaultType.TIMEOUT:
            self._inject_timeout(fault_id, duration, intensity)
        elif fault_type == FaultType.MEMORY_LEAK:
            self._inject_memory_leak(fault_id, duration, intensity)
        elif fault_type == FaultType.CPU_SPIKE:
            self._inject_cpu_spike(fault_id, duration, intensity)
        elif fault_type == FaultType.NETWORK_PARTITION:
            self._inject_network_partition(fault_id, duration, intensity)
        elif fault_type == FaultType.DATABASE_FAILURE:
            self._inject_database_failure(fault_id, duration, intensity)
        elif fault_type == FaultType.REDIS_FAILURE:
            self._inject_redis_failure(fault_id, duration, intensity)
    
    def _inject_latency(self, fault_id: str, duration: int, intensity: float):
        """Inject artificial latency"""
        def latency_wrapper(func):
            def wrapper(*args, **kwargs):
                if fault_id in self.active_faults and self.active_faults[fault_id]["active"]:
                    # Add random latency based on intensity
                    delay = random.uniform(0, intensity * 2.0)  # 0 to 2 seconds max
                    time.sleep(delay)
                    logger.debug(f"Fault {fault_id}: Injected {delay:.2f}s latency")
                return func(*args, **kwargs)
            return wrapper
        return latency_wrapper
    
    def _inject_error(self, fault_id: str, duration: int, intensity: float):
        """Inject random errors"""
        def error_wrapper(func):
            def wrapper(*args, **kwargs):
                if fault_id in self.active_faults and self.active_faults[fault_id]["active"]:
                    # Randomly throw errors based on intensity
                    if random.random() < intensity:
                        error_types = [
                            ConnectionError("Simulated connection error"),
                            TimeoutError("Simulated timeout error"),
                            ValueError("Simulated value error"),
                            RuntimeError("Simulated runtime error")
                        ]
                        error = random.choice(error_types)
                        logger.warning(f"Fault {fault_id}: Injected error {type(error).__name__}")
                        raise error
                return func(*args, **kwargs)
            return wrapper
        return error_wrapper
    
    def _inject_timeout(self, fault_id: str, duration: int, intensity: float):
        """Inject timeouts"""
        def timeout_wrapper(func):
            def wrapper(*args, **kwargs):
                if fault_id in self.active_faults and self.active_faults[fault_id]["active"]:
                    # Simulate timeout by sleeping longer than expected
                    timeout_delay = intensity * 10.0  # 0 to 10 seconds
                    time.sleep(timeout_delay)
                    logger.warning(f"Fault {fault_id}: Injected timeout delay {timeout_delay:.2f}s")
                return func(*args, **kwargs)
            return wrapper
        return timeout_wrapper
    
    def _inject_memory_leak(self, fault_id: str, duration: int, intensity: float):
        """Inject memory leak simulation"""
        def memory_leak_wrapper(func):
            def wrapper(*args, **kwargs):
                if fault_id in self.active_faults and self.active_faults[fault_id]["active"]:
                    # Simulate memory leak by allocating memory
                    leak_size = int(intensity * 1000000)  # 0 to 1MB
                    memory_leak = [0] * leak_size
                    logger.warning(f"Fault {fault_id}: Injected memory leak of {leak_size} bytes")
                    # Keep reference to prevent garbage collection
                    self.active_faults[fault_id]["memory_leak"] = memory_leak
                return func(*args, **kwargs)
            return wrapper
        return memory_leak_wrapper
    
    def _inject_cpu_spike(self, fault_id: str, duration: int, intensity: float):
        """Inject CPU spike simulation"""
        def cpu_spike_wrapper(func):
            def wrapper(*args, **kwargs):
                if fault_id in self.active_faults and self.active_faults[fault_id]["active"]:
                    # Simulate CPU spike with busy waiting
                    spike_duration = intensity * 0.1  # 0 to 100ms
                    start_time = time.time()
                    while time.time() - start_time < spike_duration:
                        pass  # Busy wait
                    logger.warning(f"Fault {fault_id}: Injected CPU spike for {spike_duration:.3f}s")
                return func(*args, **kwargs)
            return wrapper
        return cpu_spike_wrapper
    
    def _inject_network_partition(self, fault_id: str, duration: int, intensity: float):
        """Inject network partition simulation"""
        def network_partition_wrapper(func):
            def wrapper(*args, **kwargs):
                if fault_id in self.active_faults and self.active_faults[fault_id]["active"]:
                    # Simulate network partition by raising connection errors
                    if random.random() < intensity:
                        logger.warning(f"Fault {fault_id}: Simulating network partition")
                        raise ConnectionError("Simulated network partition")
                return func(*args, **kwargs)
            return wrapper
        return network_partition_wrapper
    
    def _inject_database_failure(self, fault_id: str, duration: int, intensity: float):
        """Inject database failure simulation"""
        def database_failure_wrapper(func):
            def wrapper(*args, **kwargs):
                if fault_id in self.active_faults and self.active_faults[fault_id]["active"]:
                    # Simulate database failure
                    if random.random() < intensity:
                        logger.warning(f"Fault {fault_id}: Simulating database failure")
                        raise ConnectionError("Simulated database connection failure")
                return func(*args, **kwargs)
            return wrapper
        return database_failure_wrapper
    
    def _inject_redis_failure(self, fault_id: str, duration: int, intensity: float):
        """Inject Redis failure simulation"""
        def redis_failure_wrapper(func):
            def wrapper(*args, **kwargs):
                if fault_id in self.active_faults and self.active_faults[fault_id]["active"]:
                    # Simulate Redis failure
                    if random.random() < intensity:
                        logger.warning(f"Fault {fault_id}: Simulating Redis failure")
                        raise ConnectionError("Simulated Redis connection failure")
                return func(*args, **kwargs)
            return wrapper
        return redis_failure_wrapper
    
    def stop_fault(self, fault_id: str):
        """Stop a specific fault"""
        if fault_id in self.active_faults:
            self.active_faults[fault_id]["active"] = False
            logger.info(f"Stopped fault: {fault_id}")
            
            # Update history
            for fault in self.fault_history:
                if fault["fault_id"] == fault_id:
                    fault["status"] = "stopped"
                    fault["end_time"] = time.time()
                    break
    
    def stop_all_faults(self):
        """Stop all active faults"""
        for fault_id in list(self.active_faults.keys()):
            self.stop_fault(fault_id)
        logger.info("Stopped all active faults")
    
    def get_active_faults(self) -> Dict[str, Dict[str, Any]]:
        """Get currently active faults"""
        return {k: v for k, v in self.active_faults.items() if v["active"]}
    
    def get_fault_history(self) -> List[Dict[str, Any]]:
        """Get fault injection history"""
        return self.fault_history
    
    def cleanup_expired_faults(self):
        """Clean up expired faults"""
        current_time = time.time()
        expired_faults = []
        
        for fault_id, fault_config in self.active_faults.items():
            if fault_config["active"] and current_time - fault_config["start_time"] > fault_config["duration"]:
                expired_faults.append(fault_id)
        
        for fault_id in expired_faults:
            self.stop_fault(fault_id)
            logger.info(f"Fault {fault_id} expired and was cleaned up")

# Global fault injector
fault_injector = FaultInjector()

def chaos_monkey(func):
    """Decorator to apply chaos monkey to functions"""
    def wrapper(*args, **kwargs):
        # Check for active faults that might affect this function
        active_faults = fault_injector.get_active_faults()
        
        for fault_id, fault_config in active_faults.items():
            if fault_config["fault_type"] == FaultType.LATENCY:
                delay = random.uniform(0, fault_config["intensity"] * 2.0)
                time.sleep(delay)
            elif fault_config["fault_type"] == FaultType.ERROR:
                if random.random() < fault_config["intensity"]:
                    raise random.choice([
                        ConnectionError("Chaos monkey: Connection error"),
                        TimeoutError("Chaos monkey: Timeout error"),
                        ValueError("Chaos monkey: Value error")
                    ])
        
        return func(*args, **kwargs)
    return wrapper
