from datetime import datetime
from typing import Dict, List

import httpx


class WalletService:
    """Service for managing wallet operations"""
    
    def __init__(self):
        self.transactions: List[Dict] = []
    
    async def credit_user(self, user_id: str, amount_cents: int) -> Dict:
        """Credit a user's wallet"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    "http://127.0.0.1:8000/v1/wallet/credit_qs",
                    params={"user_id": user_id, "cents": amount_cents}
                )
                
                if response.status_code == 200:
                    result = response.json()
                    transaction = {
                        'user_id': user_id,
                        'amount_cents': amount_cents,
                        'timestamp': datetime.utcnow().isoformat(),
                        'new_balance': result.get('new_balance_cents', 0)
                    }
                    self.transactions.append(transaction)
                    return result
                else:
                    raise Exception(f"Wallet credit failed: {response.status_code}")
        except Exception as e:
            raise Exception(f"Wallet service error: {str(e)}")
    
    async def get_balance(self, user_id: str) -> Dict:
        """Get user's wallet balance"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    "http://127.0.0.1:8000/v1/wallet/balance",
                    params={"user_id": user_id}
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    raise Exception(f"Balance fetch failed: {response.status_code}")
        except Exception as e:
            raise Exception(f"Wallet service error: {str(e)}")
    
    async def get_transaction_history(self, user_id: str) -> List[Dict]:
        """Get user's transaction history"""
        return [
            tx for tx in self.transactions
            if tx['user_id'] == user_id
        ]

# Global service instance
wallet_service = WalletService()
