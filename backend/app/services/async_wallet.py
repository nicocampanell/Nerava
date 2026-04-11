import asyncio
from typing import Any, Dict

import httpx


class AsyncWalletProcessor:
    def __init__(self):
        self.credit_queue = asyncio.Queue()
        self.processing = False
        self.worker_task = None
    
    async def start_worker(self):
        """Start the background worker"""
        if not self.processing:
            self.processing = True
            self.worker_task = asyncio.create_task(self._process_credits())
    
    async def stop_worker(self):
        """Stop the background worker"""
        self.processing = False
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
    
    async def queue_wallet_credit(self, user_id: str, amount_cents: int, session_id: str):
        """Queue a wallet credit job"""
        credit_request = {
            'user_id': user_id,
            'amount_cents': amount_cents,
            'session_id': session_id,
            'timestamp': asyncio.get_event_loop().time()
        }
        await self.credit_queue.put(credit_request)
    
    async def _process_credits(self):
        """Background worker to process credit queue"""
        while self.processing:
            try:
                # Wait for credit request with timeout
                credit_request = await asyncio.wait_for(
                    self.credit_queue.get(), 
                    timeout=1.0
                )
                await self._process_credit(credit_request)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Error processing credit: {e}")
    
    async def _process_credit(self, credit_request: Dict[str, Any]):
        """Process a single credit request"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    "http://127.0.0.1:8000/v1/wallet/credit_qs",
                    params={
                        "user_id": credit_request["user_id"],
                        "cents": credit_request["amount_cents"]
                    }
                )
                if response.status_code == 200:
                    print(f"Successfully credited {credit_request['amount_cents']} cents to {credit_request['user_id']}")
                else:
                    print(f"Failed to credit wallet: {response.status_code}")
        except Exception as e:
            print(f"Error crediting wallet: {e}")

# Global async wallet processor
async_wallet = AsyncWalletProcessor()
