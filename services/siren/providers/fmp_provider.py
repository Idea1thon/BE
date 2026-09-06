"""Read-only access to the middle-backend/FMP database.

Only branch metadata, submitted operating-report values, and an optional annual
brand-closure aggregate are read here. The provider deliberately does not
calculate risk and does not write to the source database.
"""

from __future__ import annotations

import datetime as dt
import re
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


_QUALIFIED_TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def _validated_table_name(value: str | None) -> str | None:
    """Allow only a schema-qualified identifier supplied by deployment config."""
    if value is None or not value.strip():
        return None
    table_name = value.strip()
    if _QUALIFIED_TABLE.fullmatch(table_name) is None:
        raise ProviderUnavailable(
            "SIREN_FRANCHISE_CLOSURE_TABLE must be a simple or schema-qualified table name"
        )
    return table_name


@dataclass(frozen=True)
class BranchSnapshot:
    branch_id: str
    franchise_id: str
    branch_name: str
    address: str
    region_code: str
    industry_code: str
    reports: list[dict[str, Any]] = field(default_factory=list)
    franchise_closure: dict[str, Any] | None = None


class FmpProvider:
    """Fetch branch and report facts from the FMP store."""

    def __init__(
        self,
        database_url: str | None,
        *,
        history_months: int = 12,
        franchise_closure_table: str | None = None,
    ) -> None:
        self.database_url = database_url.strip() if database_url else None
        self.history_months = history_months
        self.franchise_closure_table = _validated_table_name(franchise_closure_table)
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

                franchise_closure = await self._fetch_franchise_closure(
                    connection,
                    franchise_id=int(branch_row["franchise_id"]),
                    as_of=as_of,
                )

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
            franchise_closure=franchise_closure,
        )

    async def _fetch_franchise_closure(
        self, connection: Any, *, franchise_id: int, as_of: dt.date
    ) -> dict[str, Any] | None:
        """Read an optional annual brand aggregate without changing the schema.

        The current FMP schema has no annual brand-closure table. Deployments may
        point this provider at an approved read-only view/table. If it is not
        configured or does not exist, the canonical response keeps the signal
        explicitly missing instead of treating it as zero.
        """
        table_name = self.franchise_closure_table
        if table_name is None:
            return None

        exists = await connection.execute(
            text("SELECT to_regclass(:table_name) IS NOT NULL AS table_exists"),
            {"table_name": table_name},
        )
        if not bool(exists.scalar()):
            return None

        row = (
            await connection.execute(
                text(
                    f"""
                    SELECT franchise_id, year, previous_year_end_count,
                           new_openings, closures
                    FROM {table_name}
                    WHERE franchise_id = :franchise_id
                      AND year < :as_of_year
                    ORDER BY year DESC
                    LIMIT 1
                    """
                ),
                {"franchise_id": franchise_id, "as_of_year": as_of.year},
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        return {
            "franchise_id": str(row["franchise_id"]),
            "year": int(row["year"]),
            "previous_year_end_count": int(row["previous_year_end_count"]),
            "new_openings": int(row["new_openings"]),
            "closures": int(row["closures"]),
            "source": f"fmp:{table_name}",
            "synthetic": False,
        }
