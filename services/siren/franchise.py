"""브랜드의 연간 폐업 비율. 개별 점포의 폐업 확률이나 위험 점수가 아니다."""

from decimal import Decimal

from .models import FranchiseClosureResult, FranchiseClosureYear


def calculate_franchise_closure(data: FranchiseClosureYear | None) -> dict:
    if data is None:
        return FranchiseClosureResult(status="missing").model_dump()

    operating = data.previous_year_end_count + data.new_openings

    def rate(base: int) -> float | None:
        if base == 0:
            return None
        return float(round(Decimal(data.closures) / Decimal(base) * 100, 4))

    status = "calculated" if data.previous_year_end_count else (
        "partial" if operating else "not_calculable"
    )
    return FranchiseClosureResult(
        status=status,
        year=data.year,
        previous_year_end_count=data.previous_year_end_count,
        new_openings=data.new_openings,
        closures=data.closures,
        operating_base_count=operating,
        operating_base_rate_pct=rate(operating),
        previous_year_base_rate_pct=rate(data.previous_year_end_count),
        source=data.source,
        synthetic=data.synthetic,
    ).model_dump()
