"""DB_SCHEMA.md 4-0의 Enum 6종. 값은 문서와 1:1로 일치한다."""

from enum import Enum


class UserType(str, Enum):
    HQ = "HQ"
    OWNER = "OWNER"


class RegionLevel(str, Enum):
    SIDO = "SIDO"
    SIGUNGU = "SIGUNGU"
    DONG = "DONG"


class ReportStatus(str, Enum):
    DRAFT = "DRAFT"
    ANALYZING = "ANALYZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RiskLevel(str, Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    DANGER = "DANGER"


class InputSource(str, Enum):
    MANUAL = "MANUAL"
    POS = "POS"


class EmailStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
