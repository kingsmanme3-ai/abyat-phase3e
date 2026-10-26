import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .levels import LEVEL_KEYS, VISIBILITIES
from .models import Role, UnitStatus


def _check_password(v: str) -> str:
    if len(v) < 8:
        raise ValueError("كلمة المرور 8 أحرف على الأقل")
    if len(v.encode("utf-8")) > 72:
        raise ValueError("كلمة المرور طويلة جداً (الحد 72 بايت)")
    return v


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- auth / users ----------
class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChangePassword(BaseModel):
    old_password: str
    new_password: str

    _v = field_validator("new_password")(_check_password)


class UserOut(ORM):
    id: int
    username: str
    full_name: str
    role: Role
    is_active: bool
    site_ids: list[int]
    level: str = "receptionist"
    level_label: str = ""
    visibility: str = "sites"
    visibility_custom: bool = False


class MeOut(UserOut):
    caps: list[str] = []


def _check_level(v):
    if v is not None and v not in LEVEL_KEYS:
        raise ValueError("مستوى وظيفي غير معروف")
    return v


def _check_visibility(v):
    if v is not None and v not in VISIBILITIES:
        raise ValueError("نطاق رؤية غير معروف")
    return v


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    full_name: str = Field(min_length=1, max_length=150)
    password: str
    role: Role = Role.viewer
    job_level: str | None = None
    visibility: str | None = None
    site_ids: list[int] = []

    _v = field_validator("password")(_check_password)
    _l = field_validator("job_level")(_check_level)
    _vis = field_validator("visibility")(_check_visibility)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = None
    job_level: str | None = None
    visibility: str | None = None      # None مع إرساله صراحةً = إعادة لنطاق المستوى الافتراضي

    _l = field_validator("job_level")(_check_level)
    _vis = field_validator("visibility")(_check_visibility)

    @field_validator("password")
    @classmethod
    def _pw(cls, v):
        return _check_password(v) if v is not None else v


class UserSitesIn(BaseModel):
    site_ids: list[int]


# ---------- sites / buildings ----------
class SiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    code: str | None = Field(default=None, max_length=30)
    city: str | None = Field(default=None, max_length=100)


class SiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    code: str | None = Field(default=None, max_length=30)
    city: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None


class SiteOut(ORM):
    id: int
    name: str
    code: str | None
    city: str | None
    is_active: bool


class BuildingCreate(BaseModel):
    site_id: int
    name: str = Field(min_length=1, max_length=100)
    floors: int | None = Field(default=None, ge=1, le=200)


class BuildingUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    floors: int | None = Field(default=None, ge=1, le=200)
    is_active: bool | None = None


class BuildingOut(ORM):
    id: int
    site_id: int
    name: str
    floors: int | None
    is_active: bool


# ---------- units ----------
class UnitCreate(BaseModel):
    building_id: int
    number: str = Field(min_length=1, max_length=30)
    status: UnitStatus = UnitStatus.available
    tenant_name: str | None = Field(default=None, max_length=150)
    contract_end: date | None = None
    notes: str | None = None


class UnitBulk(BaseModel):
    building_id: int
    numbers: list[str] = Field(min_length=1, max_length=500)
    status: UnitStatus = UnitStatus.available


class UnitBulkOut(BaseModel):
    created: int
    skipped_existing: list[str]


class UnitUpdate(BaseModel):
    """تعديل بيانات الوحدة نفسها (للمدير). تغيير الحالة له مسار مستقل."""
    number: str | None = Field(default=None, min_length=1, max_length=30)
    notes: str | None = None


class StatusChange(BaseModel):
    status: UnitStatus
    tenant_name: str | None = Field(default=None, max_length=150)
    contract_end: date | None = None
    note: str | None = None


class UnitOut(ORM):
    id: int
    building_id: int
    site_id: int
    number: str
    status: UnitStatus
    tenant_name: str | None
    contract_end: date | None
    notes: str | None
    updated_at: datetime
    updated_by: int | None


class HistoryOut(ORM):
    id: int
    unit_id: int
    old_status: UnitStatus | None
    new_status: UnitStatus
    tenant_name: str | None
    contract_end: date | None
    note: str | None
    changed_by: int | None
    changed_at: datetime


# ---------- reports (المرحلة 3) ----------
ReportKey = Literal["units", "properties", "contracts", "users"]
_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def _check_recipients(v: list[str]) -> list[str]:
    out: list[str] = []
    for e in v:
        e = e.strip()
        if not e:
            continue
        if len(e) > 150 or not _EMAIL.match(e):
            raise ValueError(f"بريد إلكتروني غير صالح: {e}")
        if e.lower() not in out:
            out.append(e.lower())
    if len(out) > 20:
        raise ValueError("الحد الأقصى 20 مستلماً")
    return out


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    report_type: ReportKey
    site_id: int | None = None
    status: UnitStatus | None = None
    days: int | None = Field(default=None, ge=0, le=3650)
    fmt: Literal["xlsx", "csv"] = "xlsx"
    frequency: Literal["daily", "weekly", "monthly"]
    hour: int = Field(default=8, ge=0, le=23)
    weekday: int | None = Field(default=None, ge=0, le=6)
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    recipients: list[str] = []
    is_active: bool = True

    _r = field_validator("recipients")(_check_recipients)

    @model_validator(mode="after")
    def _combo(self):
        if self.frequency == "weekly" and self.weekday is None:
            raise ValueError("اختر يوم الأسبوع")
        if self.frequency == "monthly" and self.day_of_month is None:
            raise ValueError("اختر يوم الشهر (1 إلى 28)")
        if self.frequency != "weekly":
            self.weekday = None
        if self.frequency != "monthly":
            self.day_of_month = None
        return self


class ScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    report_type: ReportKey | None = None
    site_id: int | None = None
    status: UnitStatus | None = None
    days: int | None = Field(default=None, ge=0, le=3650)
    fmt: Literal["xlsx", "csv"] | None = None
    frequency: Literal["daily", "weekly", "monthly"] | None = None
    hour: int | None = Field(default=None, ge=0, le=23)
    weekday: int | None = Field(default=None, ge=0, le=6)
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    recipients: list[str] | None = None
    is_active: bool | None = None
