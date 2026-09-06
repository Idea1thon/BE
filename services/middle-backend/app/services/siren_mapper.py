"""사이렌(services/siren) 요청·응답 변환. 순수 함수만 둔다.

**정본은 `services/siren/models.py` 다.** 여기서 만드는 dict 는 그쪽
`RiskSirenRequest` 가 그대로 검증하고, 읽는 dict 는 그쪽 `analyze()` 가 만든
결과다. 우리가 형태를 추측해서 쓰면 계약이 어긋나도 테스트가 못 잡는다 —
PR #26 에서 `explanations` 를 list 로 가정했다가 실제로는 dict 였던 적이 있다.

프레임워크를 임포트하지 않는다(표준 라이브러리 + 우리 enum 만). DB 세션도
HTTP 도 모르는 순수 변환이라 테스트가 가볍고, 계약이 바뀌면 어디가 깨지는지가
한 파일 안에서 드러난다.
"""

from __future__ import annotations

import datetime as dt
import decimal
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import InputSource, RiskLevel

# --------------------------------------------------------------------------- #
# 1. 입력 항목 35개 → BranchMonthlyReport
# --------------------------------------------------------------------------- #
# 우리 `report_input_field.code` 와 사이렌 `BranchMonthlyReport` 가 1:1 이다.
# 같은 요구사항 정의서를 보고 각자 만들었는데 항목·그룹 구성이 그대로 맞았다.
#
# 값이 없는 항목은 0.0 으로 채운다. 사이렌 모델은 각 블록의 필드를 전부 요구하고
# (`extra="forbid"`, 기본값 없음) 우리 필수 항목은 8개뿐이라 나머지 27개는
# 제출되지 않을 수 있다. "지출이 없었다" 와 "모른다" 를 구분하는 계약이 양쪽
# 어디에도 없으므로 0 으로 둔다. 이 선택은 SR-05 비용 비율에 영향을 준다.
FIELD_TO_SIREN: dict[str, tuple[str, ...]] = {
    "HALL_CARD": ("sales", "hall", "credit"),
    "HALL_CASH": ("sales", "hall", "cash"),
    "HALL_EASYPAY": ("sales", "hall", "simple_pay"),
    "DLV_BAEMIN": ("sales", "delivery", "baemin"),
    "DLV_COUPANG": ("sales", "delivery", "coupang_eats"),
    "DLV_ETC": ("sales", "delivery", "other"),
    "TOGO_CARD": ("sales", "takeout", "credit"),
    "TOGO_CASH": ("sales", "takeout", "cash"),
    "TOGO_EASYPAY": ("sales", "takeout", "simple_pay"),
    "DED_REFUND": ("deductions", "customer_refund"),
    "DED_COUPON": ("deductions", "own_coupon_discount"),
    "MAT_FOOD": ("cogs", "food_ingredients"),
    "MAT_SUB": ("cogs", "sub_materials"),
    "BEV_ALCOHOL": ("cogs", "alcohol"),
    "BEV_DRINK": ("cogs", "beverage"),
    "INV_BEGIN": ("cogs", "inventory_begin"),
    "INV_END": ("cogs", "inventory_end"),
    "LAB_FULLTIME": ("labor", "fulltime"),
    "LAB_PARTTIME": ("labor", "parttime"),
    "LAB_INSURANCE": ("labor", "four_major_insurance"),
    "LAB_WELFARE": ("labor", "meal_welfare"),
    "LAB_SHORTTERM": ("labor", "short_term"),
    "VAR_PLATFORM_FEE": ("variable", "platform_fee"),
    "VAR_DELIVERY_FEE": ("variable", "delivery_agency_fee"),
    "VAR_SUPPLIES": ("variable", "supplies"),
    "VAR_UTILITY": ("variable", "utilities"),
    "VAR_MARKETING": ("variable", "marketing_ad"),
    "OPS_RENT": ("opex", "rent_mgmt"),
    "OPS_RENTAL": ("opex", "equipment_rental"),
    "OPS_TELECOM": ("opex", "telecom_it"),
    "OPS_ACCOUNTING": ("opex", "tax_bookkeeping"),
    "OPS_INSURANCE": ("opex", "insurance"),
    "OPS_CARD_FEE": ("opex", "card_fee"),
    "FIN_LOAN_INTEREST": ("finance", "loan_interest"),
    "FIN_MISC": ("finance", "other_misc"),
}

# 사이렌 SourceTag. synthetic_* 은 데모 합성 데이터용이라 우리가 붙이지 않는다 —
# 실제 점주 입력을 합성으로 표기하면 data_provenance 공시가 거짓이 된다.
_SOURCE_TAG = {InputSource.POS: "pos", InputSource.MANUAL: "self_reported"}

_EMPTY_BLOCKS: dict[str, Any] = {
    "sales": {
        "hall": {"credit": 0.0, "cash": 0.0, "simple_pay": 0.0},
        "delivery": {"baemin": 0.0, "coupang_eats": 0.0, "other": 0.0},
        "takeout": {"credit": 0.0, "cash": 0.0, "simple_pay": 0.0},
    },
    "deductions": {"customer_refund": 0.0, "own_coupon_discount": 0.0},
    "cogs": {
        "food_ingredients": 0.0, "sub_materials": 0.0, "alcohol": 0.0,
        "beverage": 0.0, "inventory_begin": 0.0, "inventory_end": 0.0,
    },
    "labor": {
        "fulltime": 0.0, "parttime": 0.0, "four_major_insurance": 0.0,
        "meal_welfare": 0.0, "short_term": 0.0,
    },
    "variable": {
        "platform_fee": 0.0, "delivery_agency_fee": 0.0, "supplies": 0.0,
        "utilities": 0.0, "marketing_ad": 0.0,
    },
    "opex": {
        "rent_mgmt": 0.0, "equipment_rental": 0.0, "telecom_it": 0.0,
        "tax_bookkeeping": 0.0, "insurance": 0.0, "card_fee": 0.0,
    },
    "finance": {"loan_interest": 0.0, "other_misc": 0.0},
}


def _blank_blocks() -> dict[str, Any]:
    """매번 새 dict 를 만든다. 공유하면 한 달 값이 다른 달로 샌다."""
    return {
        "sales": {k: dict(v) for k, v in _EMPTY_BLOCKS["sales"].items()},
        **{k: dict(v) for k, v in _EMPTY_BLOCKS.items() if k != "sales"},
    }


class SirenMappingError(ValueError):
    """사이렌 요청을 만들 수 없다. 상대 서비스 장애가 아니라 우리 입력 문제다."""


def build_monthly_report(
    *,
    report_month: dt.date,
    items: dict[str, decimal.Decimal | float | int],
    input_source: InputSource,
) -> dict[str, Any]:
    """보고서 1건 → `BranchMonthlyReport` dict.

    정의되지 않은 코드가 오면 조용히 버리지 않는다. 어차피 상대가 422 를 주는데,
    그때는 원인이 우리 코드 어디인지 안 보인다.
    """
    unknown = sorted(set(items) - set(FIELD_TO_SIREN))
    if unknown:
        raise SirenMappingError(f"사이렌 계약에 대응이 없는 입력 항목: {', '.join(unknown)}")

    blocks = _blank_blocks()
    for code, amount in items.items():
        path = FIELD_TO_SIREN[code]
        target = blocks
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = float(amount)

    tag = _SOURCE_TAG[input_source]
    return {
        "month": f"{report_month.year:04d}-{report_month.month:02d}",
        **blocks,
        "sales_source": tag,
        "cost_source": tag,
    }


# --------------------------------------------------------------------------- #
# 2. 점포 → BranchLocation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LocationInputs:
    """사이렌이 필수로 요구하는데 `branch` 테이블에 없는 값들.

    `trade_area_code`·`x_5181`·`y_5181` 는 `BranchLocation` 에서 필수다
    (`services/siren/models.py`). 우리 점포는 주소 문자열만 갖고 있어서 호출부가
    바깥에서 넣어 줘야 한다. 조달 경로는 아직 사람이 정하지 않았다.
    """

    trade_area_code: str
    x_5181: float
    y_5181: float
    admin_dong_code: str | None = None


def build_location(*, region_code: str, inputs: LocationInputs) -> dict[str, Any]:
    """`BranchLocation` dict.

    행정표준코드에서 행정동은 10자리·시군구는 5자리이고 앞 5자리가 시군구다.
    시군구 코드를 그대로 받은 경우도 5자리라 같은 규칙이 적용된다.
    """
    gu_code = (region_code or "").strip()[:5]
    if len(gu_code) != 5:
        raise SirenMappingError(f"자치구 코드를 만들 수 없습니다: region_code={region_code!r}")
    if not inputs.trade_area_code.strip():
        raise SirenMappingError("trade_area_code 는 사이렌 필수 입력입니다")
    return {
        "gu_code": gu_code,
        "admin_dong_code": inputs.admin_dong_code,
        "trade_area_code": inputs.trade_area_code,
        "x_5181": float(inputs.x_5181),
        "y_5181": float(inputs.y_5181),
    }


# --------------------------------------------------------------------------- #
# 3. 분석 요청 전체
# --------------------------------------------------------------------------- #
def build_analyze_request(
    *,
    request_id: str,
    franchise_id: int,
    branch_id: int,
    as_of: dt.date,
    industry_code: str,
    location: dict[str, Any],
    monthly_reports: list[dict[str, Any]],
    brand_name: str | None = None,
    market_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`RiskSirenRequest` dict.

    - 식별자는 문자열이다. 우리는 BIGSERIAL 이라 str() 로 넘긴다. 사이렌은 이 값을
      저장·비교하지 않고 응답에 그대로 돌려주기만 한다.
    - `llm_mode` 를 명시적으로 끈다. 상대 기본값은 `"explanation_only"` 라
      지정하지 않으면 LLM 을 부른다. 우리는 LLM 을 구성하지 않았다.
    - `send_notifications` 는 False 를 명시한다. 상대는 true 를 받으면 ValueError
      를 던지고, 알림 생성·발송은 CONTRACT.md 상 우리 책임이다.
    - `market_data` 가 없으면 시장 층이 `missing` 이 되어 종합 점수·등급이 null 인
      partial 결과가 온다. 실패가 아니라 계약된 동작이다.
    """
    payload: dict[str, Any] = {
        "request_id": request_id,
        "franchise_id": str(franchise_id),
        "branch_id": str(branch_id),
        "as_of": as_of.isoformat(),
        "industry_code": industry_code,
        "location": location,
        "branch_reports": monthly_reports,
        "options": {"llm_mode": "disabled", "send_notifications": False},
    }
    if brand_name:
        payload["brand_name"] = brand_name
    if market_data is not None:
        payload["market_data"] = market_data
    return payload


# --------------------------------------------------------------------------- #
# 4. 응답 → report_analysis 컬럼값
# --------------------------------------------------------------------------- #
# 사이렌 등급(한글) → 우리 risk_level enum. 경계값도 일치한다:
# 사이렌 normal_upper_bound=40 / caution_upper_bound=70,
# INTERFACE_SPEC 4장 0~39 / 40~69 / 70~100.
GRADE_TO_RISK_LEVEL = {
    "정상": RiskLevel.NORMAL,
    "주의": RiskLevel.CAUTION,
    "위험": RiskLevel.DANGER,
}

# DB_SCHEMA 4-9 `rule_version VARCHAR(20)`.
RULE_VERSION_MAX_LENGTH = 20


@dataclass
class AnalysisValues:
    """`report_analysis` 에 넣을 값. 저장 가능 여부를 함께 들고 다닌다.

    현행 스키마는 `risk_score`·`risk_level` 이 NOT NULL 이고 `rule_version` 이
    VARCHAR(20) 인데, 사이렌은 데이터가 부족하면 점수·등급을 의도적으로 null 로
    주고 `score_version` 은 20자를 넘는다. 값을 지어내거나 잘라 넣으면
    "데이터 부족" 이 "정상" 으로 바뀌고 재현성 정보가 깨진다. 그래서 변환은
    사실대로 하고, 저장 가능 여부는 `blockers` 로 알린다.
    """

    risk_score: int | None
    risk_level: RiskLevel | None
    factors: dict[str, Any]
    risk_periods: list[Any]
    recommendations: list[Any]
    rule_version: str
    calculated_at: dt.datetime
    calculation_status: str
    blockers: list[str] = field(default_factory=list)

    @property
    def storable(self) -> bool:
        """현행 DB_SCHEMA 4-9 제약 그대로 INSERT 할 수 있는가."""
        return not self.blockers


def to_analysis_values(
    response: dict[str, Any], *, calculated_at: dt.datetime | None = None
) -> AnalysisValues:
    """`analyze()` 응답 → `report_analysis` 컬럼값.

    `factors` 에 `components` 를 그대로 넣는다. DB_SCHEMA 4-9 가 factors 를
    "4요소별 값·가중치·기여도" 로 정의했고 사이렌 `components` 가 정확히
    closure·sales_decline·competition·profitability 4요소다.
    """
    risk = response.get("risk") or {}
    score = risk.get("score")
    grade = risk.get("grade")
    status = str(risk.get("calculation_status") or "partial")

    blockers: list[str] = []

    risk_score: int | None = None
    if score is not None:
        # float → SMALLINT. 상대는 소수 4자리를 주고 우리 컬럼은 정수다.
        # INTERFACE_SPEC 4장이 risk_score 를 integer 0~100 으로 규정한다.
        risk_score = int(round(float(score)))
    else:
        blockers.append("risk_score 가 null 인데 report_analysis.risk_score 는 NOT NULL")

    risk_level: RiskLevel | None = None
    if grade is not None:
        if grade not in GRADE_TO_RISK_LEVEL:
            raise SirenMappingError(f"알 수 없는 위험 등급: {grade!r}")
        risk_level = GRADE_TO_RISK_LEVEL[grade]
    else:
        blockers.append("risk_level 이 null 인데 report_analysis.risk_level 은 NOT NULL")

    rule_version = str(risk.get("score_version") or "")
    if len(rule_version) > RULE_VERSION_MAX_LENGTH:
        blockers.append(
            f"rule_version 이 {len(rule_version)}자로 "
            f"VARCHAR({RULE_VERSION_MAX_LENGTH}) 초과: {rule_version}"
        )

    projections = response.get("projections") or {}
    owner_projection = projections.get("branch_owner") or {}

    return AnalysisValues(
        risk_score=risk_score,
        risk_level=risk_level,
        factors=response.get("components") or {},
        # 사이렌 응답에 당월·3·6·12개월 위험도(REQ-SRN-02)에 해당하는 필드가 없다.
        # 없는 값을 만들지 않고 빈 배열로 둔다. NOT NULL 은 만족한다.
        risk_periods=[],
        recommendations=list(owner_projection.get("recommended_actions") or []),
        rule_version=rule_version,
        calculated_at=calculated_at or dt.datetime.now(dt.timezone.utc),
        calculation_status=status,
        blockers=blockers,
    )


# --------------------------------------------------------------------------- #
# 5. 알림 (REQ-SRN-12, CONTRACT.md — 생성·발송은 우리 책임)
# --------------------------------------------------------------------------- #
def should_notify(response: dict[str, Any]) -> bool:
    """알림을 만들어야 하는가.

    사이렌 `alert.should_fire` 를 그대로 따른다. 종합 등급이 '위험' 인 경우뿐
    아니라, 시장 자료가 없어 등급이 미확정이어도 확인된 점포 수익성 위험이면
    참이 된다(alert_policy `confirmed-branch-v1`). 등급만 보면 그 경고를 놓친다.
    """
    alert = response.get("alert") or {}
    return bool(alert.get("should_fire"))


def notification_message(response: dict[str, Any], *, branch_name: str) -> str:
    """인앱 알림 문구. 등급이 미확정인 경우를 '위험' 으로 표기하지 않는다."""
    risk = response.get("risk") or {}
    grade = risk.get("grade")
    if grade:
        return f"{branch_name}의 전월 위험도 분석 결과가 '{grade}' 단계입니다."
    basis = ((response.get("alert") or {}).get("trigger") or {}).get("basis")
    detail = {"branch_risk": "가맹점 위험", "profitability": "수익성 위험"}.get(basis, "위험 신호")
    return (
        f"{branch_name}에서 확인된 {detail}가 감지되었습니다. "
        "시장 자료가 부족해 종합 등급은 확정되지 않았습니다."
    )
