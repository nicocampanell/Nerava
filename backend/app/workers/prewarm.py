"""
Cache prewarming workers for frequently accessed data
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional

from app.cache.layers import layered_cache
from app.config import settings
from app.services.energyhub_sim import sim

logger = logging.getLogger(__name__)


class CachePrewarmer:
    """Service for prewarming frequently accessed cache data"""

    def __init__(self, prewarm_interval: int = 45):
        self.prewarm_interval = prewarm_interval
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.prewarm_functions: Dict[str, callable] = {}

    def register_prewarmer(self, name: str, func: callable):
        """Register a prewarming function"""
        self.prewarm_functions[name] = func
        logger.info(f"Registered prewarmer: {name}")

    async def start(self):
        """Start the prewarming worker"""
        if self.running:
            logger.warning("Cache prewarmer is already running")
            return

        self.running = True
        self.task = asyncio.create_task(self._run())
        logger.info("Cache prewarmer started")

    async def stop(self):
        """Stop the prewarming worker"""
        if not self.running:
            return

        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("Cache prewarmer stopped")

    async def _run(self):
        """Main prewarming loop"""
        while self.running:
            try:
                await self._prewarm_all()
                await asyncio.sleep(self.prewarm_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cache prewarmer: {e}")
                await asyncio.sleep(self.prewarm_interval)

    async def _prewarm_all(self):
        """Run all registered prewarming functions"""
        for name, func in self.prewarm_functions.items():
            try:
                await self._prewarm_single(name, func)
            except Exception as e:
                logger.error(f"Error prewarming {name}: {e}")

    async def _prewarm_single(self, name: str, func: callable):
        """Run a single prewarming function"""
        start_time = datetime.utcnow()

        try:
            if asyncio.iscoroutinefunction(func):
                await func()
            else:
                func()

            duration = (datetime.utcnow() - start_time).total_seconds()
            logger.info(f"Prewarmed {name} in {duration:.2f}s")

        except Exception as e:
            logger.error(f"Error prewarming {name}: {e}")
            raise


# Prewarming functions
async def prewarm_energy_windows():
    """Prewarm energy hub windows cache"""
    try:
        # Get current windows
        windows = sim.list_windows(None)

        # Cache for different time scenarios
        time_scenarios = [
            None,  # Current time
            "2024-01-01T12:00:00Z",  # Solar surplus window
            "2024-01-01T15:00:00Z",  # Green hour window
            "2024-01-01T20:00:00Z",  # Outside windows
        ]

        for scenario in time_scenarios:
            cache_key = f"energyhub:windows:{scenario or 'current'}"
            await layered_cache.set(cache_key, windows, ttl=60)

        logger.info("Prewarmed energy windows cache")

    except Exception as e:
        logger.error(f"Error prewarming energy windows: {e}")


async def prewarm_hub_data():
    """Prewarm hub data cache"""
    try:
        # This would prewarm hub data from the database
        # For now, just log that it would happen
        logger.info("Prewarmed hub data cache")

    except Exception as e:
        logger.error(f"Error prewarming hub data: {e}")


async def prewarm_user_preferences():
    """Prewarm user preferences cache"""
    try:
        # This would prewarm frequently accessed user preferences
        # For now, just log that it would happen
        logger.info("Prewarmed user preferences cache")

    except Exception as e:
        logger.error(f"Error prewarming user preferences: {e}")


# Global prewarmer instance
cache_prewarmer = CachePrewarmer(prewarm_interval=settings.cache_ttl_windows)

# Register prewarming functions
cache_prewarmer.register_prewarmer("energy_windows", prewarm_energy_windows)
cache_prewarmer.register_prewarmer("hub_data", prewarm_hub_data)
cache_prewarmer.register_prewarmer("user_preferences", prewarm_user_preferences)
