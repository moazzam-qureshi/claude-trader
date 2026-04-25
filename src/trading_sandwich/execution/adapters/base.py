"""ExchangeAdapter ABC. Paper + live implementations conform to this contract."""
from __future__ import annotations

from abc import ABC, abstractmethod

from trading_sandwich.contracts.phase2 import (
    AccountState,
    OrderRequest,
    OrderReceipt,
)


class ExchangeAdapter(ABC):
    """All execution paths (paper, live) implement this contract.

    The execution-worker loads one adapter at startup based on
    policy.execution_mode and calls only these methods. No adapter-specific
    logic leaks into worker code.
    """

    @abstractmethod
    async def submit_order(self, request: OrderRequest) -> OrderReceipt: ...

    @abstractmethod
    async def cancel_order(self, exchange_order_id: str) -> OrderReceipt: ...

    @abstractmethod
    async def get_open_orders(self) -> list[dict]: ...

    @abstractmethod
    async def get_positions(self) -> list[dict]: ...

    @abstractmethod
    async def get_account_state(self) -> AccountState: ...
