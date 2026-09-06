"""Read-only access to the middle-backend/FMP database.

Only branch metadata and submitted operating-report values are read here. The
provider deliberately does not calculate risk and does not write to the source
database.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


class ProviderUnavailable(RuntimeError):
    """The provider could not reach or query its configured source."""


class SourceNotFound(LookupError):
    """The requested source entity does not exist."""


def _async_database_url(value: str) -> str:
    value = value.strip()
    if value.startswith("postgresql+asyncpg://"):
        return value
    if value.startswith("postgresql://"):
        return "postgresql+asyncpg://" + value.removeprefix("postgresql://")
    if value.startswith("postgres://"):
        return "postgresql+asyncpg://" + value.removeprefix("postgres://")
    return value


@dataclass(frozen=True)
class BranchSnapshot:
    branch_id: str
    franchise_id: str
    branch_name: str
    address: str
    region_code: str
    industry_code: str
    reports: list[dict[str, Any]] = field(default_factory=list)


class FmpProvider:
    """Fetch branch and report facts from the FMP store."""

    def __init__(self, database_url: str | None, *, history_months: int = 12) -> None:
        self.database_url = database_url.strip() if database_url else None
        self.history_months = history_months
        self._engine: AsyncEngine | None = None

    def _get_engine(self) -> AsyncEngine:
        if not self.database_url:
            raise ProviderUnavailable("FMP_DATABASE_URL is not configured")
        if self._engine is None:
            self._engine = create_async_engine(_async_database_url(self.database_url), pool_pre_ping=True)
        return self._engine

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    @staticmethod
    def _branch_key(value: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise SourceNotFound(f"branch_id must be numeric for FMP: {value!r}") from exc

    async def fetch_branch(self, branch_id: str, as_of: dt.date) -> BranchSnapshot:
        numeric_branch_id = self._branch_key(branch_id)
        try:
            async with self._get_engine().connect() as connection:
                branch_row = (
                    await connection.execute(
                        text(
                            """
                            SELECT id, franchise_id, name, address, region_code,
                                   business_category_code
                            FROM branch
                            WHERE id = :branch_id
                            """
                        ),
                        {"branch_id": numeric_branch_id},
                    )
                ).mappings().one_or_none()
                if branch_row is None:
                    raise SourceNotFound(f"branch not found: {branch_id}")

                rows = (
                    await connection.execute(
                        text(
                            """
                            SELECT r.id AS report_id, r.report_month, r.input_source,
                                   i.field_code, i.amount
                            FROM operation_report AS r
                            LEFT JOIN report_input_item AS i ON i.report_id = r.id
                            WHERE r.branch_id = :branch_id
                              AND r.report_month <= :as_of
                            ORDER BY r.report_month DESC, r.id DESC
                            """
                        ),
                        {"branch_id": numeric_branch_id, "as_of": as_of},
                    )
                ).mappings().all()
        except (SourceNotFound, ProviderUnavailable):
            raise
        except SQLAlchemyError as exc:
            raise ProviderUnavailable("FMP database query failed") from exc

        by_report: dict[int, dict[str, Any]] = {}
        for row in rows:
            report_id = int(row["report_id"])
            report = by_report.setdefault(
                report_id,
                {
                    "month": row["report_month"],
                    "input_source": row["input_source"],
                    "items": {},
                },
            )
            if row["field_code"] is not None:
                report["items"][str(row["field_code"])] = float(row["amount"])

        reports = list(by_report.values())
        reports.sort(key=lambda item: item["month"])
        if self.history_months > 0:
            reports = reports[-self.history_months :]

        return BranchSnapshot(
            branch_id=str(branch_row["id"]),
            franchise_id=str(branch_row["franchise_id"]),
            branch_name=str(branch_row["name"]),
            address=str(branch_row["address"]),
            region_code=str(branch_row["region_code"]),
            industry_code=str(branch_row["business_category_code"]),
            reports=reports,
        )
