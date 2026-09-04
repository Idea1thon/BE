"""API 호출 예산 가드. 무료 구간 안에서만 호출하도록 강제.

    from _budget import Budget
    b = Budget("naver_datalab", monthly_limit=28000, per_run_limit=200)
    b.check()          # 한도 초과면 SystemExit
    ... API 호출 ...
    b.commit()         # 성공 시 1 증가·저장

``daily_limit``을 지정하면 일간·월간·실행당 한도를 함께 검사한다.
카운터는 프로젝트 루트 .api_budget.json (gitignore)에 저장한다. 기존 월간
카운터 형식은 첫 commit 때 새 형식으로 자동 이관한다. 호출자가 실제 HTTP
시도 직전에 commit하면 재시도·HTTP 오류도 보수적으로 세는 데 쓸 수 있다.
"""
from __future__ import annotations
import datetime as _dt
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUDGET_FILE = os.path.join(ROOT, ".api_budget.json")


def _load() -> dict:
    try:
        with open(BUDGET_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    # 프로세스별 고유 tmp — 동시에 여러 이식 스크립트가 돌 때 os.replace 경쟁 방지
    tmp = f"{BUDGET_FILE}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, BUDGET_FILE)


class Budget:
    def __init__(
        self,
        service: str,
        monthly_limit: int,
        per_run_limit: int = 500,
        daily_limit: int | None = None,
    ):
        self.service = service
        self.monthly_limit = int(monthly_limit)
        self.per_run_limit = int(per_run_limit)
        self.daily_limit = int(daily_limit) if daily_limit is not None else None
        if (
            self.monthly_limit < 1
            or self.per_run_limit < 1
            or (self.daily_limit is not None and self.daily_limit < 1)
        ):
            raise ValueError("호출 한도는 1 이상이어야 합니다.")
        self._run = 0

    @property
    def month(self) -> str:
        return _dt.date.today().strftime("%Y-%m")

    @property
    def day(self) -> str:
        return _dt.date.today().isoformat()

    def _service_counts(self, data: dict) -> tuple[dict, dict]:
        """새 형식과 기존 ``{month: count}`` 형식을 모두 읽는다."""
        service_data = data.get(self.service, {})
        if "monthly" in service_data or "daily" in service_data:
            return service_data.get("monthly", {}), service_data.get("daily", {})
        return service_data, {}

    def _monthly_count(self) -> int:
        monthly, _ = self._service_counts(_load())
        return int(monthly.get(self.month, 0))

    def _daily_count(self) -> int:
        _, daily = self._service_counts(_load())
        return int(daily.get(self.day, 0))

    def remaining(self) -> int:
        """하위 호환용 월간 잔여 호출 수."""
        return max(0, self.monthly_limit - self._monthly_count())

    def daily_remaining(self) -> int | None:
        if self.daily_limit is None:
            return None
        return max(0, self.daily_limit - self._daily_count())

    def check(self) -> None:
        monthly_used = self._monthly_count()
        if monthly_used >= self.monthly_limit:
            raise SystemExit(
                f"[{self.service}] {self.month} 월 호출 {monthly_used}/{self.monthly_limit} — 한도 도달. "
                f"중단합니다. (한도는 .env 의 관련 LIMIT 또는 {os.path.basename(BUDGET_FILE)} 로 조정)"
            )
        if self.daily_limit is not None:
            daily_used = self._daily_count()
            if daily_used >= self.daily_limit:
                raise SystemExit(
                    f"[{self.service}] {self.day} 일 호출 {daily_used}/{self.daily_limit} — 한도 도달. "
                    f"중단합니다. (한도는 .env 의 관련 LIMIT 또는 {os.path.basename(BUDGET_FILE)} 로 조정)"
                )
        if self._run >= self.per_run_limit:
            raise SystemExit(
                f"[{self.service}] 이번 실행에서 {self._run}회 호출 — per_run_limit({self.per_run_limit}) 도달. 중단."
            )

    def commit(self) -> None:
        data = _load()
        svc = data.setdefault(self.service, {})
        # 기존 월간 단순 dict를 {monthly, daily}로 한 번만 이관한다.
        if "monthly" not in svc and "daily" not in svc:
            legacy_months = {key: value for key, value in svc.items() if isinstance(value, int)}
            svc.clear()
            svc["monthly"] = legacy_months
            svc["daily"] = {}
        monthly = svc.setdefault("monthly", {})
        daily = svc.setdefault("daily", {})
        monthly[self.month] = int(monthly.get(self.month, 0)) + 1
        if self.daily_limit is not None:
            daily[self.day] = int(daily.get(self.day, 0)) + 1
        _save(data)
        self._run += 1

    def snapshot(self) -> dict[str, int | str | None]:
        return {
            "service": self.service,
            "day": self.day,
            "daily_used": self._daily_count() if self.daily_limit is not None else None,
            "daily_limit": self.daily_limit,
            "month": self.month,
            "monthly_used": self._monthly_count(),
            "monthly_limit": self.monthly_limit,
            "per_run_used": self._run,
            "per_run_limit": self.per_run_limit,
        }

    def status(self) -> str:
        if self.daily_limit is None:
            return f"{self.service} {self.month}: {self._monthly_count()}/{self.monthly_limit} (남은 {self.remaining()})"
        return (
            f"{self.service} 일 {self.day}: {self._daily_count()}/{self.daily_limit} "
            f"(남은 {self.daily_remaining()}) | 월 {self.month}: "
            f"{self._monthly_count()}/{self.monthly_limit} (남은 {self.remaining()})"
        )


if __name__ == "__main__":
    data = _load()
    if not data:
        print("아직 기록 없음")
    for service in sorted(data):
        service_data = data[service]
        if "monthly" in service_data or "daily" in service_data:
            for day, count in sorted(service_data.get("daily", {}).items()):
                print(f"{service} 일 {day}: {count}")
            for month, count in sorted(service_data.get("monthly", {}).items()):
                print(f"{service} 월 {month}: {count}")
        else:
            for month, count in sorted(service_data.items()):
                print(f"{service} 월 {month}: {count}")
