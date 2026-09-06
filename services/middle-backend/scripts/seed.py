"""개발용 시드.

회원가입 API가 이번 Phase 범위 밖이므로 계정은 이 스크립트로만 생성한다.
비밀번호는 bcrypt 해시로 저장한다. 평문은 저장하지 않는다.

실행: `uv run python -m scripts.seed`
멱등: 이미 있으면 건너뛴다.
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import func, select

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import (
    Branch,
    BusinessCategory,
    FinancialProduct,
    Franchise,
    Region,
    ReportInputField,
    UserAccount,
)
from app.models.enums import RegionLevel, RiskLevel, UserType

# 이 스크립트는 알려진 자격 증명의 계정을 만든다. 운영 DB에 실행되면 그 자체가 사고다.
# 그래서 DB에 연결하기 전에 환경과 비밀번호를 확인하고, 기본 비밀번호는 두지 않는다.
_ALLOWED_ENVIRONMENTS = frozenset({"development", "test"})


def _guarded_password() -> str:
    if settings.environment not in _ALLOWED_ENVIRONMENTS:
        raise SystemExit(
            f"seed는 {sorted(_ALLOWED_ENVIRONMENTS)} 환경에서만 실행할 수 있다 "
            f"(ENVIRONMENT={settings.environment!r})."
        )
    # settings 를 거쳐 읽는다. .env 는 pydantic-settings 가 읽으므로 os.environ 에는 없다.
    password = settings.seed_password.strip()
    if not password:
        raise SystemExit("SEED_PASSWORD가 필요하다. .env에 개발용 비밀번호를 설정한다.")
    return password

# F11: branch.region_code에는 시군구 코드만 들어간다.
# 서울 25개 자치구 전부. 행정표준코드 시군구 5자리이며 `GET /regions` 가 code 순으로
# 정렬하므로 드롭다운도 행정구역 표준 순서(종로구→강동구)로 나온다.
#
# 행정동은 두 곳만 둔다. 추천 파이프라인은 서울 전역을 읽지만 우리가 검증한 조합은
# 이 둘뿐이고, 데이터가 없는 동을 목록에 올리면 심사자가 후보 0건을 보게 된다.
# FE 는 동을 강제하지 않는다(`행정동 (선택)`) — 구만 골라도 검색된다.
REGIONS = [
    ("11", None, RegionLevel.SIDO, "서울특별시"),
    ("11110", "11", RegionLevel.SIGUNGU, "종로구"),
    ("11140", "11", RegionLevel.SIGUNGU, "중구"),
    ("11170", "11", RegionLevel.SIGUNGU, "용산구"),
    ("11200", "11", RegionLevel.SIGUNGU, "성동구"),
    ("11215", "11", RegionLevel.SIGUNGU, "광진구"),
    ("11230", "11", RegionLevel.SIGUNGU, "동대문구"),
    ("11260", "11", RegionLevel.SIGUNGU, "중랑구"),
    ("11290", "11", RegionLevel.SIGUNGU, "성북구"),
    ("11305", "11", RegionLevel.SIGUNGU, "강북구"),
    ("11320", "11", RegionLevel.SIGUNGU, "도봉구"),
    ("11350", "11", RegionLevel.SIGUNGU, "노원구"),
    ("11380", "11", RegionLevel.SIGUNGU, "은평구"),
    ("11410", "11", RegionLevel.SIGUNGU, "서대문구"),
    ("11440", "11", RegionLevel.SIGUNGU, "마포구"),
    ("11470", "11", RegionLevel.SIGUNGU, "양천구"),
    ("11500", "11", RegionLevel.SIGUNGU, "강서구"),
    ("11530", "11", RegionLevel.SIGUNGU, "구로구"),
    ("11545", "11", RegionLevel.SIGUNGU, "금천구"),
    ("11560", "11", RegionLevel.SIGUNGU, "영등포구"),
    ("11590", "11", RegionLevel.SIGUNGU, "동작구"),
    ("11620", "11", RegionLevel.SIGUNGU, "관악구"),
    ("11650", "11", RegionLevel.SIGUNGU, "서초구"),
    ("11680", "11", RegionLevel.SIGUNGU, "강남구"),
    ("11710", "11", RegionLevel.SIGUNGU, "송파구"),
    ("11740", "11", RegionLevel.SIGUNGU, "강동구"),
    ("1168010100", "11680", RegionLevel.DONG, "역삼1동"),
    ("1144012000", "11440", RegionLevel.DONG, "서교동"),
]

# 업종 코드는 추천 API·위험 사이렌과 같은 체계를 쓴다 (services/recommendation-api,
# services/siren 의 IndustryCode). 서비스 간 변환 계층을 두지 않기 위해 정본을 맞춘다.
CATEGORIES = [
    ("CS100001", "한식음식점"),
    ("CS100002", "중식음식점"),
    ("CS100003", "일식음식점"),
    ("CS100004", "양식음식점"),
    ("CS100005", "제과점"),
    ("CS100006", "패스트푸드점"),
    ("CS100007", "치킨전문점"),
    ("CS100008", "분식전문점"),
    ("CS100009", "호프-간이주점"),
    ("CS100010", "커피-음료"),
]

# REQ v0.11 「보고서 입력 항목 정의」 35개 항목 (그룹 11개, 필수 9개)
INPUT_FIELDS: list[tuple[str, str, str, bool]] = [
    ("HALL_CARD", "신용카드(홀)", "홀 매출", True),
    ("HALL_CASH", "현금(홀)", "홀 매출", True),
    ("HALL_EASYPAY", "간편결제(홀)", "홀 매출", False),
    ("DLV_BAEMIN", "배달의 민족", "배달 매출", False),
    ("DLV_COUPANG", "쿠팡이츠", "배달 매출", False),
    ("DLV_ETC", "기타(배달)", "배달 매출", False),
    ("TOGO_CARD", "신용카드(포장)", "포장 매출", False),
    ("TOGO_CASH", "현금(포장)", "포장 매출", False),
    ("TOGO_EASYPAY", "간편결제", "포장 매출", False),
    ("DED_REFUND", "고객 환불", "매출 차감 항목", True),
    ("DED_COUPON", "자체 할인 쿠폰 적용액", "매출 차감 항목", False),
    ("MAT_FOOD", "당월 식자재", "식자재", True),
    ("MAT_SUB", "부자재 매입", "식자재", False),
    ("BEV_ALCOHOL", "주류", "주류/음료", False),
    ("BEV_DRINK", "음료", "주류/음료", False),
    ("INV_BEGIN", "기초 재고액", "재고액", False),
    ("INV_END", "기말 재고액", "재고액", False),
    ("LAB_FULLTIME", "정규직 급여", "인건비", False),
    ("LAB_PARTTIME", "파트타임 급여", "인건비", False),
    ("LAB_INSURANCE", "4대 보험료", "인건비", False),
    ("LAB_WELFARE", "식대/복리후생비", "인건비", False),
    ("LAB_SHORTTERM", "단기 인력 급여", "인건비", False),
    ("VAR_PLATFORM_FEE", "플랫폼 수수료", "변동비", False),
    ("VAR_DELIVERY_FEE", "배달 대행료", "변동비", False),
    ("VAR_SUPPLIES", "소모품비", "변동비", False),
    ("VAR_UTILITY", "수도광열비", "변동비", True),
    ("VAR_MARKETING", "마케팅/광고비", "변동비", False),
    ("OPS_RENT", "임차료/관리비", "운영비", True),
    ("OPS_RENTAL", "기기 렌탈료", "운영비", False),
    ("OPS_TELECOM", "통신/IT 비용", "운영비", True),
    ("OPS_ACCOUNTING", "세무/기장 대행료", "운영비", False),
    ("OPS_INSURANCE", "보험료", "운영비", True),
    ("OPS_CARD_FEE", "카드 수수료", "운영비", True),
    ("FIN_LOAN_INTEREST", "대출이자", "금융 및 기타", False),
    ("FIN_MISC", "기타 잡비", "금융 및 기타", False),
]

ACCOUNTS = [
    ("hq@example.com", "김본사", UserType.HQ, None),
    ("owner1@example.com", "이점주", UserType.OWNER, ("강남 역삼점", "서울특별시 강남구 역삼동 1-1", "11680", "CS100001")),
    ("owner2@example.com", "박점주", UserType.OWNER, ("마포 서교점", "서울특별시 마포구 서교동 2-2", "11440", "CS100010")),
]

# REQ-OW-07 등급별 노출 상품. REQ-OW-10이 "현재는 하드코딩 또는 DB 직접 입력"으로 규정한다.
# (target_risk_level, name, description, link_url, display_order)
FINANCIAL_PRODUCTS = [
    (RiskLevel.DANGER, "긴급 경영안정자금", "폐업 위험 단계 사업장 대상 긴급 운영자금", "https://example.com/products/emergency", 1),
    (RiskLevel.DANGER, "소상공인 재기 지원 보증", "상환 유예와 보증을 함께 제공", "https://example.com/products/recovery", 2),
    (RiskLevel.DANGER, "임차료 부담 완화 대출", "임차료 비중이 높은 사업장 대상", "https://example.com/products/rent-relief", 3),
    (RiskLevel.DANGER, "노출되지 않아야 하는 4번째 상품", "REQ-OW-09 최대 3개 제한 확인용", "https://example.com/products/overflow", 4),
    (RiskLevel.CAUTION, "운영 안정화 대출", "주의 단계 사업장 대상 저금리 대출", "https://example.com/products/stabilize", 1),
    (RiskLevel.CAUTION, "매출 변동 보험", "매출 급감 구간을 보전", "https://example.com/products/insurance", 2),
    (RiskLevel.NORMAL, "성장 투자 지원", "정상 운영 사업장의 확장 투자 지원", "https://example.com/products/growth", 1),
]


async def seed() -> None:
    dev_password = _guarded_password()  # DB 연결 전에 막는다
    async with SessionLocal() as session:
        # 기준정보
        for code, parent, level, name in REGIONS:
            if not await session.get(Region, code):
                session.add(Region(code=code, parent_code=parent, level=level, name=name))
        for code, name in CATEGORIES:
            if not await session.get(BusinessCategory, code):
                session.add(BusinessCategory(code=code, name=name))
        for order, (code, name, group, required) in enumerate(INPUT_FIELDS, start=1):
            if not await session.get(ReportInputField, code):
                session.add(
                    ReportInputField(
                        code=code,
                        name=name,
                        group_name=group,
                        is_required=required,
                        display_order=order,
                    )
                )
        await session.flush()

        # 본사
        franchise = (
            await session.execute(select(Franchise).where(Franchise.name == "테스트프랜차이즈"))
        ).scalar_one_or_none()
        if franchise is None:
            franchise = Franchise(name="테스트프랜차이즈")
            session.add(franchise)
            await session.flush()

        # 계정 + 점포
        password_hash = hash_password(dev_password)
        for email, name, user_type, branch_info in ACCOUNTS:
            existing = (
                await session.execute(select(UserAccount).where(UserAccount.email == email))
            ).unique().scalar_one_or_none()
            if existing is not None:
                continue
            user = UserAccount(
                franchise_id=franchise.id,
                email=email,
                password_hash=password_hash,
                user_type=user_type,
                name=name,
            )
            session.add(user)
            await session.flush()

            if branch_info is not None:
                b_name, address, region_code, category_code = branch_info
                session.add(
                    Branch(
                        franchise_id=franchise.id,
                        owner_user_id=user.id,
                        name=b_name,
                        address=address,
                        region_code=region_code,
                        business_category_code=category_code,
                    )
                )

        # 금융상품 (REQ-OW-10)
        existing_products = (
            await session.execute(select(func.count()).select_from(FinancialProduct))
        ).scalar_one()
        if existing_products == 0:
            for level, name, description, url, order in FINANCIAL_PRODUCTS:
                session.add(
                    FinancialProduct(
                        target_risk_level=level,
                        name=name,
                        description=description,
                        link_url=url,
                        display_order=order,
                    )
                )

        await session.commit()

    print("seed 완료")
    print(f"  계정: {', '.join(a[0] for a in ACCOUNTS)}")
    print("  비밀번호: SEED_PASSWORD 값 — bcrypt 해시로만 저장됨")
    print(f"  입력 항목: {len(INPUT_FIELDS)}개 (필수 {sum(1 for f in INPUT_FIELDS if f[3])}개)")
    print(f"  금융상품: {len(FINANCIAL_PRODUCTS)}개")


if __name__ == "__main__":
    asyncio.run(seed())
