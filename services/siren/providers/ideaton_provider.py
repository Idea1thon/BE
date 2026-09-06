"""Read-only IDEATON location and commercial-signal provider."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .fmp_provider import BranchSnapshot, ProviderUnavailable, _async_database_url


@dataclass(frozen=True)
class MarketSnapshot:
    location: dict[str, Any]
    market_data: dict[str, Any] | None


def _quarter_label(value: object) -> str | None:
    raw = str(value or "")
    if len(raw) == 5 and raw.isdigit():
        return f"{raw[:4]}Q{raw[4]}"
    if len(raw) == 6 and raw[:4].isdigit() and raw[4] == "Q":
        return raw
    return None


def _nonnegative_int_or_none(value: object) -> int | None:
    """Preserve NULL/invalid source counts; never reinterpret them as zero."""
    if value is None:
        return None
    parsed = int(value)
    return parsed if parsed >= 0 else None


class IdeatonProvider:
    """Resolve branch location and market signals in IDEATON.

    The provider uses PostGIS functions already used by the recommendation
    service. It is read-only and returns ``None`` for market data only when the
    source has no matching facts; database failures are surfaced as 503-worthy
    provider errors.
    """

    def __init__(
        self,
        database_url: str | None,
        *,
        competition_radius_m: float = 250.0,
        similar_industry_codes: Sequence[str] = (),
    ) -> None:
        self.database_url = database_url.strip() if database_url else None
        self.competition_radius_m = competition_radius_m
        self.similar_industry_codes = tuple(str(code) for code in similar_industry_codes)
        self._engine: AsyncEngine | None = None

    def _get_engine(self) -> AsyncEngine:
        if not self.database_url:
            raise ProviderUnavailable("IDEATON_DATABASE_URL is not configured")
        if self._engine is None:
            self._engine = create_async_engine(_async_database_url(self.database_url), pool_pre_ping=True)
        return self._engine

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    async def fetch_market(self, branch: BranchSnapshot, as_of: dt.date) -> MarketSnapshot:
        try:
            async with self._get_engine().connect() as connection:
                location = await self._resolve_location(connection, branch)
                trade_area_code = location.get("trade_area_code")
                if not trade_area_code:
                    return MarketSnapshot(location=location, market_data=None)

                closure_rows = (
                    await connection.execute(
                        text(
                            """
                            SELECT period, total_store_count, open_store_count,
                                   close_store_count, is_partial_latest
                            FROM location.store_quarter
                            WHERE spatial_unit_type = 'commercial_area'
                              AND spatial_unit_code = :area_code
                              AND industry_code = :industry_code
                            ORDER BY period
                            """
                        ),
                        {"area_code": trade_area_code, "industry_code": branch.industry_code},
                    )
                ).mappings().all()
                sales_rows = (
                    await connection.execute(
                        text(
                            """
                            SELECT period, sales_amount
                            FROM location.sales_quarter
                            WHERE spatial_unit_type = 'commercial_area'
                              AND spatial_unit_code = :area_code
                              AND industry_code = :industry_code
                            ORDER BY period
                            """
                        ),
                        {"area_code": trade_area_code, "industry_code": branch.industry_code},
                    )
                ).mappings().all()

                competition = await self._competition(
                    connection,
                    x=location.get("x_5181"),
                    y=location.get("y_5181"),
                    industry_code=branch.industry_code,
                    as_of=as_of,
                )
        except ProviderUnavailable:
            raise
        except SQLAlchemyError as exc:
            raise ProviderUnavailable("IDEATON database query failed") from exc

        closure_quarters = []
        for row in closure_rows:
            quarter = _quarter_label(row["period"])
            if quarter is None:
                continue
            closure_quarters.append(
                {
                    "quarter": quarter,
                    "active_count_end": _nonnegative_int_or_none(row["total_store_count"]),
                    "new_openings": _nonnegative_int_or_none(row["open_store_count"]),
                    "closures": _nonnegative_int_or_none(row["close_store_count"]),
                    "quarter_status": (
                        "부분"
                        if row["is_partial_latest"]
                        or any(
                            value is None
                            for value in (
                                row["total_store_count"],
                                row["open_store_count"],
                                row["close_store_count"],
                            )
                        )
                        else "완전"
                    ),
                }
            )
        sales_quarters = []
        for row in sales_rows:
            quarter = _quarter_label(row["period"])
            if quarter is None or row["sales_amount"] is None:
                continue
            amount = float(row["sales_amount"])
            if amount < 0:
                # 음수 매출을 0원으로 바꾸면 시장 하락 위험을 숨긴다.
                # 해당 분기를 제외해 이후 계산에서 missing/partial로 드러낸다.
                continue
            sales_quarters.append({"quarter": quarter, "amount_krw": amount})

        market_data: dict[str, Any] | None = None
        if closure_quarters or sales_quarters or competition is not None:
            all_quarters = [q["quarter"] for q in closure_quarters + sales_quarters]
            market_data = {
                "source": "ideaton.location",
                "closure_source": "ideaton.location.store_quarter",
                "sales_source": "ideaton.location.sales_quarter",
                "as_of_quarter": max(all_quarters) if all_quarters else None,
                "closure_quarters": closure_quarters,
                "sales_quarters": sales_quarters,
                "competition": competition,
            }
        return MarketSnapshot(location=location, market_data=market_data)

    async def _resolve_location(self, connection: Any, branch: BranchSnapshot) -> dict[str, Any]:
        row = (
            await connection.execute(
                text(
                    """
                    SELECT b.admin_dong_code,
                           b.host_area_id,
                           ST_X(b.point) AS x_5181,
                           ST_Y(b.point) AS y_5181,
                           a.sigungu_code
                    FROM context.commercial_building AS b
                    LEFT JOIN location.area AS a
                      ON a.area_id = b.host_area_id
                    LEFT JOIN LATERAL (
                      SELECT l.mgm_bldrgst_pk
                      FROM context.commercial_building_link AS l
                      WHERE l.pnu = b.pnu
                        AND NOT EXISTS (
                          SELECT 1
                          FROM context.commercial_building_link AS l2
                          WHERE l2.pnu = l.pnu
                            AND l2.mgm_bldrgst_pk <> l.mgm_bldrgst_pk
                        )
                      LIMIT 1
                    ) AS lk ON true
                    LEFT JOIN context.building_register AS r
                      ON r.mgm_bldrgst_pk = lk.mgm_bldrgst_pk
                    WHERE b.lot_address = :address
                       OR r.road_address = :address
                       OR EXISTS (
                          SELECT 1
                          FROM context.commercial_building_link AS l3
                          WHERE l3.pnu = b.pnu AND l3.lot_address = :address
                       )
                    ORDER BY CASE b.area_join_type
                               WHEN '내부' THEN 0
                               WHEN '근접' THEN 1
                               ELSE 2
                             END,
                             b.host_area_id
                    LIMIT 1
                    """
                ),
                {"address": branch.address},
            )
        ).mappings().one_or_none()

        gu_code = str((row or {}).get("sigungu_code") or branch.region_code)[:5]
        location = {
            "gu_code": gu_code,
            "admin_dong_code": (row or {}).get("admin_dong_code"),
            "trade_area_code": None,
            "x_5181": None,
            "y_5181": None,
        }
        if row is not None:
            host_area_id = str(row.get("host_area_id") or "")
            location.update(
                {
                    "trade_area_code": host_area_id.removeprefix("commercial_area:"),
                    "x_5181": float(row["x_5181"]) if row["x_5181"] is not None else None,
                    "y_5181": float(row["y_5181"]) if row["y_5181"] is not None else None,
                }
            )
        return location

    async def _competition(
        self,
        connection: Any,
        *,
        x: float | None,
        y: float | None,
        industry_code: str,
        as_of: dt.date,
    ) -> dict[str, Any] | None:
        if x is None or y is None:
            return None

        recent_start = as_of - dt.timedelta(days=90)
        previous_start = as_of - dt.timedelta(days=180)
        similar = list(self.similar_industry_codes)
        if similar:
            placeholders = ", ".join(f":similar_{index}" for index in range(len(similar)))
            similar_recent_predicate = (
                f"industry_code IN ({placeholders}) "
                "AND licensed_at >= :recent_start AND licensed_at <= :as_of"
            )
            similar_previous_predicate = (
                f"industry_code IN ({placeholders}) "
                "AND licensed_at >= :previous_start AND licensed_at < :recent_start"
            )
        else:
            similar_recent_predicate = "FALSE"
            similar_previous_predicate = "FALSE"
        similar_params = {f"similar_{index}": code for index, code in enumerate(similar)}
        result = (
            await connection.execute(
                text(
                    f"""
                    SELECT
                      COUNT(*) FILTER (
                        WHERE industry_code = :industry_code
                          AND licensed_at >= :recent_start
                          AND licensed_at <= :as_of
                      ) AS same_recent,
                      COUNT(*) FILTER (
                        WHERE industry_code = :industry_code
                          AND licensed_at >= :previous_start
                          AND licensed_at < :recent_start
                      ) AS same_previous,
                      COUNT(*) FILTER (
                        WHERE {similar_recent_predicate}
                      ) AS similar_recent,
                      COUNT(*) FILTER (
                        WHERE {similar_previous_predicate}
                      ) AS similar_previous
                    FROM location.permitted_establishment
                    WHERE ST_DWithin(
                      point,
                      ST_SetSRID(ST_MakePoint(:x, :y), 5181),
                      :radius_m
                    )
                      AND (closed_at IS NULL OR closed_at > :as_of)
                    """
                ),
                {
                    "industry_code": industry_code,
                    "recent_start": recent_start,
                    "previous_start": previous_start,
                    "as_of": as_of,
                    "x": x,
                    "y": y,
                    "radius_m": self.competition_radius_m,
                    **similar_params,
                },
            )
        ).mappings().one()
        return {
            "radius_m": self.competition_radius_m,
            "same_industry_new_recent_3m": int(result["same_recent"] or 0),
            "same_industry_new_previous_3m": int(result["same_previous"] or 0),
            "similar_industry_new_recent_3m": int(result["similar_recent"] or 0),
            "similar_industry_new_previous_3m": int(result["similar_previous"] or 0),
            "similar_industry_codes": similar,
            "active_only": True,
            "source": "ideaton.location.permitted_establishment",
        }
