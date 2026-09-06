"""Review provider boundary.

The current repositories do not expose a review source table. Keeping this
provider explicit means review enrichment can be added without putting review
queries into the orchestrator or the deterministic calculator.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol


class ReviewProvider(Protocol):
    async def fetch_reviews(self, branch_id: str, as_of: date) -> dict[str, Any] | None:
        ...

    async def close(self) -> None:
        ...


class NullReviewProvider:
    async def fetch_reviews(self, branch_id: str, as_of: date) -> None:
        return None

    async def close(self) -> None:
        return None
