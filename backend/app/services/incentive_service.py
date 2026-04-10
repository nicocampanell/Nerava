from datetime import datetime
from typing import Dict, List, Optional

from app.services.energyhub_sim import sim


class IncentiveService:
    """Service for managing incentive windows and rewards"""
    
    def __init__(self):
        self.windows = sim.windows
    
    async def get_active_windows(self) -> List[Dict]:
        """Get all available windows"""
        return sim.list_windows(None)
    
    async def get_active_window(self) -> Optional[Dict]:
        """Get currently active window"""
        now = datetime.utcnow()
        for window in self.windows:
            if window.is_active(now):
                return {
                    'id': window.id,
                    'label': window.label,
                    'price_per_kwh': window.price_per_kwh,
                    'multiplier': window.multiplier,
                    'active_now': True
                }
        return None
    
    async def calculate_reward(self, kwh: float, window: Optional[Dict] = None) -> Dict:
        """Calculate reward for given kWh and window"""
        if not window:
            window = await self.get_active_window()
        
        if not window:
            return {
                'grid_reward_usd': 0.0,
                'merchant_reward_usd': 0.75,  # Base co-fund
                'total_reward_usd': 0.75
            }
        
        grid_reward = kwh * window['price_per_kwh'] * window['multiplier']
        merchant_reward = 0.75  # Base co-fund
        total = grid_reward + merchant_reward
        
        return {
            'grid_reward_usd': round(grid_reward, 2),
            'merchant_reward_usd': round(merchant_reward, 2),
            'total_reward_usd': round(total, 2),
            'window_applied': window['id']
        }

# Global service instance
incentive_service = IncentiveService()
