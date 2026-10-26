import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .levels import level_label, level_of, visibility_of


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UnitStatus(str, enum.Enum):
    rented = "rented"                  # مؤجرة
    available = "available"            # متاحة
    temp_reserved = "temp_reserved"    # محجوزة مؤقتاً
    development = "development"        # تطوير
    cleaning = "cleaning"              # نظافة
    maintenance = "maintenance"        # صيانة


STATUS_LABELS_AR = {
    UnitStatus.rented: "مؤجرة",
    UnitStatus.available: "متاحة",
    UnitStatus.temp_reserved: "محجوزة مؤقتاً",
    UnitStatus.development: "تطوير",
    UnitStatus.cleaning: "نظافة",
    UnitStatus.maintenance: "صيانة",
}


class Role(str, enum.Enum):
    admin = "admin"      # مدير
    updater = "updater"  # محدّث لمواقع محددة
    viewer = "viewer"    # مشاهد


def _enum(e):
    return Enum(e, native_enum=False, length=20, values_callable=lambda x: [m.value for m in x])


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    code: Mapped[str | None] = mapped_column(String(30), unique=True)
    city: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    buildings: Mapped[list["Building"]] = relationship(back_populates="site")


class Building(Base):
    __tablename__ = "buildings"
    __table_args__ = (UniqueConstraint("site_id", "name", name="uq_building_site_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    floors: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    site: Mapped[Site] = relationship(back_populates="buildings")
    units: Mapped[list["Unit"]] = relationship(back_populates="building")


class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (UniqueConstraint("building_id", "number", name="uq_unit_building_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.id"), index=True)
    number: Mapped[str] = mapped_column(String(30))
    status: Mapped[UnitStatus] = mapped_column(_enum(UnitStatus), default=UnitStatus.available, index=True)
    tenant_name: Mapped[str | None] = mapped_column(String(150))
    contract_end: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    building: Mapped[Building] = relationship(back_populates="units")

    @property
    def site_id(self) -> int:
        return self.building.site_id


class UnitStatusHistory(Base):
    __tablename__ = "unit_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"), index=True)
    old_status: Mapped[UnitStatus | None] = mapped_column(_enum(UnitStatus))
    new_status: Mapped[UnitStatus] = mapped_column(_enum(UnitStatus))
    tenant_name: Mapped[str | None] = mapped_column(String(150))
    contract_end: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    full_name: Mapped[str] = mapped_column(String(150))
    password_hash: Mapped[str] = mapped_column(String(100))
    role: Mapped[Role] = mapped_column(_enum(Role), default=Role.viewer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    job_level: Mapped[str | None] = mapped_column(String(20))            # 3هـ: أحد المستويات الستة
    visibility_override: Mapped[str | None] = mapped_column(String(10))  # 3هـ: تعديل المدير لنطاق الرؤية

    site_links: Mapped[list["UserSite"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def level(self) -> str:
        return level_of(self.job_level, self.role.value)

    @property
    def level_label(self) -> str:
        return level_label(self.level)

    @property
    def visibility(self) -> str:
        return visibility_of(self.visibility_override, self.level, self.role.value)

    @property
    def visibility_custom(self) -> bool:
        return self.visibility_override is not None

    @property
    def site_ids(self) -> list[int]:
        return sorted(l.site_id for l in self.site_links)


class UserSite(Base):
    __tablename__ = "user_sites"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), primary_key=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(50))
    entity: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class DailySnapshot(Base):
    """لقطة يومية لأعداد الحالات لكل موقع، تغذّي الرسم المساحي في لوحة الإشغال."""
    __tablename__ = "daily_snapshots"
    __table_args__ = (UniqueConstraint("day", "site_id", name="uq_snapshot_day_site"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    rented: Mapped[int] = mapped_column(Integer, default=0)
    available: Mapped[int] = mapped_column(Integer, default=0)
    temp_reserved: Mapped[int] = mapped_column(Integer, default=0)
    development: Mapped[int] = mapped_column(Integer, default=0)
    cleaning: Mapped[int] = mapped_column(Integer, default=0)
    maintenance: Mapped[int] = mapped_column(Integer, default=0)


class ReportSchedule(Base):
    """جدولة تقرير دوري (يومي/أسبوعي/شهري) يُولَّد تلقائياً ويُرسل بالبريد إن كان مُعدّاً."""
    __tablename__ = "report_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    report_type: Mapped[str] = mapped_column(String(30))
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"))
    status: Mapped[str | None] = mapped_column(String(20))      # فلتر لتقرير الوحدات
    days: Mapped[int | None] = mapped_column(Integer)           # فلتر لتقرير العقود
    fmt: Mapped[str] = mapped_column(String(10), default="xlsx")
    frequency: Mapped[str] = mapped_column(String(10))          # daily | weekly | monthly
    hour: Mapped[int] = mapped_column(Integer, default=8)       # بالتوقيت المحلي (TZ_OFFSET_HOURS)
    weekday: Mapped[int | None] = mapped_column(Integer)        # 0=الأحد ... 6=السبت
    day_of_month: Mapped[int | None] = mapped_column(Integer)   # 1..28
    recipients: Mapped[str | None] = mapped_column(Text)        # بريد مفصول بفواصل
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReportRun(Base):
    """سجل تشغيلات التقارير (المجدولة واليدوية) مع اسم الملف المحفوظ."""
    __tablename__ = "report_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey("report_schedules.id"), index=True)
    report_type: Mapped[str] = mapped_column(String(30))
    site_id: Mapped[int | None] = mapped_column(Integer)
    fmt: Mapped[str] = mapped_column(String(10))
    file_name: Mapped[str | None] = mapped_column(String(80))      # اسم الملف على القرص
    download_name: Mapped[str] = mapped_column(String(200), default="")
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(10), default="ok")  # ok | failed
    error: Mapped[str | None] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(String(10), default="manual")   # schedule | manual
    emailed: Mapped[bool] = mapped_column(Boolean, default=False)
    email_note: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


# ======================= المرحلة 3ب: نظام كتابة التقارير =======================
class ReportCategory(Base):
    """بطاقة تقارير (النوع). kind=data لتقارير البيانات التلقائية، authored لما يكتبه الموظفون."""
    __tablename__ = "report_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(10), default="authored")   # data | authored
    description: Mapped[str | None] = mapped_column(Text)
    icon: Mapped[str | None] = mapped_column(String(8))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Department(Base):
    """قطاع / إدارة (يمكن ربط الإدارة بقطاع عبر parent_id)."""
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ReportTemplate(Base):
    """قالب نوع التقرير وحقوله. كل تعديل ينشئ إصداراً جديداً، والتقارير القديمة تحتفظ بنسختها."""
    __tablename__ = "report_templates"
    __table_args__ = (UniqueConstraint("category_id", "version", name="uq_template_cat_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("report_categories.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer, default=1)
    schema_json: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CategoryPermission(Base):
    """من يكتب / يقرأ / يعتمد في بطاقة: حسب الدور (role) أو مستخدم محدد (user_id)."""
    __tablename__ = "category_permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("report_categories.id"), index=True)
    role: Mapped[str | None] = mapped_column(String(20))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    can_write: Mapped[bool] = mapped_column(Boolean, default=False)
    can_read: Mapped[bool] = mapped_column(Boolean, default=False)
    can_review: Mapped[bool] = mapped_column(Boolean, default=False)   # يُستخدم في 3د


class ReportCounter(Base):
    """عدّاد الرقم المسلسل لكل سنة (RPT-2026-000123)."""
    __tablename__ = "report_counters"

    year: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last: Mapped[int] = mapped_column(Integer, default=0)


class AuthoredReport(Base):
    """تقرير كتبه موظف. الرقم المسلسل يُمنح عند الإرسال فقط (المسودة بلا رقم)."""
    __tablename__ = "authored_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    serial: Mapped[str | None] = mapped_column(String(20), unique=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("report_categories.id"), index=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("report_templates.id"))
    template_version: Mapped[int] = mapped_column(Integer, default=1)
    schema_json: Mapped[str] = mapped_column(Text)               # نسخة الحقول وقت الكتابة
    title: Mapped[str] = mapped_column(String(200))
    body_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)  # draft | submitted | reviewed
    priority: Mapped[str] = mapped_column(String(10), default="normal")
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), index=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ======================= المرحلة 3ج: مكتبة الوسائط =======================
class AppSetting(Base):
    """إعدادات صغيرة بصيغة مفتاح/قيمة (مثل: أي بطاقات يظهر فيها تبويب المكتبة، وعلامة بذر التصنيفات)."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class MediaCategory(Base):
    """تصنيف في مكتبة الوسائط (الصيانة، النظافة، النجارة، وما يضيفه المدير)."""
    __tablename__ = "media_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    color: Mapped[str] = mapped_column(String(7), default="#0f766e")
    sort: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MediaItem(Base):
    """صورة أو فيديو. report_id يربطه بتقرير مكتوب (اختياري). الملف على القرص باسم عشوائي."""
    __tablename__ = "media_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("media_categories.id"), index=True)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("authored_reports.id"), index=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), index=True)
    kind: Mapped[str] = mapped_column(String(6))                    # image | video
    mime: Mapped[str] = mapped_column(String(40))
    file_name: Mapped[str] = mapped_column(String(60), unique=True)
    original_name: Mapped[str] = mapped_column(String(200), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    caption: Mapped[str | None] = mapped_column(String(300))
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


# ======================= المرحلة 3هـ: المستويات والمهام =======================
class LevelCapability(Base):
    """خانة في مصفوفة الصلاحيات (مستوى × صلاحية). غير الموجود يُؤخذ من الافتراضي."""
    __tablename__ = "level_capabilities"

    level: Mapped[str] = mapped_column(String(20), primary_key=True)
    capability: Mapped[str] = mapped_column(String(30), primary_key=True)
    allowed: Mapped[bool] = mapped_column(Boolean, default=False)


class UnitTask(Base):
    """مهمة على وحدة (نظافة، صيانة…) مسندة لمستخدم."""
    __tablename__ = "unit_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"), index=True)
    assignee_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="other")
    title: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)   # open | done
    due_date: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
