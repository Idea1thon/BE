"""Review providers for the auxiliary SR-04 signal.

The SQL provider is deliberately read-only. A missing table or a branch with
no rows returns ``None`` so the core calculator preserves the existing
``review_signal.status=missing`` behaviour instead of treating missing reviews
as a zero-risk observation.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .fmp_provider import (
    ProviderUnavailable,
    _async_database_url,
    _validated_table_name,
)


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


class SqlReviewProvider:
    """Read branch reviews from the configured FMP/IDEATON database."""

    _ALLOWED_SOURCES = {"synthetic_reviews", "naver_place", "kakao_map", "delivery_app"}

    def __init__(self, database_url: str | None, *, table: str | None = None) -> None:
        self.database_url = database_url.strip() if database_url else None
        self.table = _validated_table_name(table or "public.siren_review")
        self._engine: AsyncEngine | None = None

    @staticmethod
    def _branch_key(value: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ProviderUnavailable(f"branch_id must be numeric for reviews: {value!r}") from exc

    def _get_engine(self) -> AsyncEngine:
        if not self.database_url:
            raise ProviderUnavailable("FMP_DATABASE_URL is not configured")
        if self._engine is None:
            self._engine = create_async_engine(
                _async_database_url(self.database_url), pool_pre_ping=True
            )
        return self._engine

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    async def fetch_reviews(self, branch_id: str, as_of: date) -> dict[str, Any] | None:
        numeric_branch_id = self._branch_key(branch_id)
        try:
            async with self._get_engine().connect() as connection:
                exists = await connection.execute(
                    text("SELECT to_regclass(:table_name) IS NOT NULL AS table_exists"),
                    {"table_name": self.table},
                )
                if not bool(exists.scalar()):
                    return None
                rows = (
                    await connection.execute(
                        text(
                            f"""
                            SELECT review_id, branch_id, written_at, rating,
                                   review_text, sentiment_label, source
                            FROM {self.table}
                            WHERE branch_id = :branch_id
                              AND written_at <= :as_of
                            ORDER BY written_at, review_id
                            """
                        ),
                        {"branch_id": numeric_branch_id, "as_of": as_of},
                    )
                ).mappings().all()
        except ProviderUnavailable:
            raise
        except SQLAlchemyError as exc:
            raise ProviderUnavailable("review database query failed") from exc

        if not rows:
            return None

        source = str(rows[0]["source"])
        if source not in self._ALLOWED_SOURCES:
            raise ProviderUnavailable(f"unsupported review source: {source!r}")
        if any(str(row["source"]) != source for row in rows):
            raise ProviderUnavailable("multiple review sources for one branch are not supported")

        return {
            "source": source,
            "records": [
                {
                    "review_id": str(row["review_id"]),
                    "branch_id": str(row["branch_id"]),
                    "written_at": row["written_at"],
                    "rating": int(row["rating"]),
                    "text": str(row["review_text"]),
                    "sentiment_label": str(row["sentiment_label"]),
                }
                for row in rows
            ],
        }
