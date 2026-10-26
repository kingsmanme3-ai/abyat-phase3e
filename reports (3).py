"""تعريف التقارير واستعلاماتها (المرحلة 3). كل تقرير يعيد (الأعمدة، الصفوف)."""
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import STATUS_LABELS_AR, Building, Site, Unit, UnitStatus, User
from .report_engine import Columns
from .snapshots import STATUS_KEYS, _tz, today

ROLE_LABELS = {"admin": "مدير", "updater": "محدّث", "viewer": "مشاهد"}


def _nat(s: str) -> list:
    """ترتيب طبيعي: 2 قبل 10، و A2 قبل A10."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s or "")]


def _val(x):
    return x.value if hasattr(x, "value") else x


def _status_label(st) -> str:
    return STATUS_LABELS_AR[UnitStatus(_val(st))]


def _local_str(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_tz()).strftime("%Y-%m-%d %H:%M")


def _contract_state(left: int | None) -> str:
    if left is None:
        return "بلا تاريخ"
    if left < 0:
        return "منتهٍ"
    if left <= 30:
        return "ينتهي خلال 30 يوماً"
    if left <= 60:
        return "31 – 60 يوماً"
    if left <= 90:
        return "61 – 90 يوماً"
    return "أكثر من 90 يوماً"


def _unit_rows(db: Session, site_id: int | None, rented_only: bool = False,
               status: UnitStatus | str | None = None):
    stmt = (select(Unit, Building.name, Site.name)
            .join(Building, Building.id == Unit.building_id)
            .join(Site, Site.id == Building.site_id)
            .where(Site.is_active.is_(True)))
    if site_id:
        stmt = stmt.where(Site.id == site_id)
    if rented_only:
        stmt = stmt.where(Unit.status == UnitStatus.rented)
    elif status:
        stmt = stmt.where(Unit.status == UnitStatus(_val(status)))
    return db.execute(stmt).all()


# ---------------- تقرير الوحدات ----------------
def units_report(db: Session, site_id=None, status=None, days=None):
    t = today()
    out = []
    for u, bname, sname in _unit_rows(db, site_id, status=status):
        left = (u.contract_end - t).days if u.contract_end else None
        out.append({"site": sname, "building": bname, "unit": u.number,
                    "status": _status_label(u.status), "tenant": u.tenant_name or "",
                    "contract_end": u.contract_end, "days_left": left,
                    "notes": u.notes or "", "updated_at": _local_str(u.updated_at)})
    out.sort(key=lambda r: (r["site"], r["building"], _nat(r["unit"])))
    cols: Columns = [("site", "الموقع"), ("building", "العمارة"), ("unit", "الوحدة"),
                     ("status", "الحالة"), ("tenant", "المستأجر"), ("contract_end", "انتهاء العقد"),
                     ("days_left", "الأيام المتبقية"), ("notes", "ملاحظات"), ("updated_at", "آخر تحديث")]
    return cols, out


# ---------------- تقرير العقارات (عمارة بعمارة) ----------------
def properties_report(db: Session, site_id=None, status=None, days=None):
    bstmt = (select(Building.id, Building.name, Building.floors, Site.name)
             .join(Site, Site.id == Building.site_id)
             .where(Site.is_active.is_(True), Building.is_active.is_(True)))
    if site_id:
        bstmt = bstmt.where(Site.id == site_id)
    counts: dict[int, dict[str, int]] = {}
    for bid, st, n in db.execute(
            select(Unit.building_id, Unit.status, func.count(Unit.id)).group_by(Unit.building_id, Unit.status)):
        counts.setdefault(bid, {k: 0 for k in STATUS_KEYS})[_val(st)] = n
    out = []
    for bid, bname, floors, sname in db.execute(bstmt).all():
        c = counts.get(bid) or {k: 0 for k in STATUS_KEYS}
        total = sum(c.values())
        row = {"site": sname, "building": bname, "floors": floors, "total": total, **c,
               "occupancy": round(c["rented"] * 100 / total, 1) if total else 0}
        out.append(row)
    out.sort(key=lambda r: (r["site"], _nat(r["building"])))
    cols: Columns = ([("site", "الموقع"), ("building", "العمارة"), ("floors", "الأدوار"), ("total", "إجمالي الوحدات")]
                     + [(k, STATUS_LABELS_AR[UnitStatus(k)]) for k in STATUS_KEYS]
                     + [("occupancy", "نسبة الإشغال %")])
    return cols, out


# ---------------- تقرير العقود ----------------
def contracts_report(db: Session, site_id=None, status=None, days=None):
    """الوحدات المؤجرة وتاريخ انتهاء عقودها. days: يُبقي المنتهية وما ينتهي خلال هذا العدد من الأيام."""
    t = today()
    out = []
    for u, bname, sname in _unit_rows(db, site_id, rented_only=True):
        left = (u.contract_end - t).days if u.contract_end else None
        if days is not None and (left is None or left > days):
            continue
        out.append({"site": sname, "building": bname, "unit": u.number, "tenant": u.tenant_name or "",
                    "contract_end": u.contract_end, "days_left": left, "state": _contract_state(left)})
    out.sort(key=lambda r: (r["days_left"] is None, r["days_left"] or 0, r["site"], r["building"], _nat(r["unit"])))
    cols: Columns = [("site", "الموقع"), ("building", "العمارة"), ("unit", "الوحدة"), ("tenant", "المستأجر"),
                     ("contract_end", "انتهاء العقد"), ("days_left", "الأيام المتبقية"), ("state", "الحالة")]
    return cols, out


# ---------------- تقرير المستخدمين (للمدير) ----------------
def users_report(db: Session, site_id=None, status=None, days=None):
    names = {s.id: s.name for s in db.scalars(select(Site))}
    out = []
    for u in db.scalars(select(User).order_by(User.username)).all():
        role = _val(u.role)
        if role == "admin":
            sites = "كل المواقع"
        elif role == "updater":
            sites = "، ".join(sorted(names.get(i, f"#{i}") for i in u.site_ids)) or "—"
        else:
            sites = "عرض فقط"
        out.append({"username": u.username, "full_name": u.full_name, "role": ROLE_LABELS.get(role, role),
                    "active": "فعّال" if u.is_active else "معطّل", "sites": sites,
                    "created_at": _local_str(u.created_at)})
    cols: Columns = [("username", "المستخدم"), ("full_name", "الاسم"), ("role", "الدور"),
                     ("active", "الحالة"), ("sites", "المواقع"), ("created_at", "تاريخ الإنشاء")]
    return cols, out


@dataclass(frozen=True)
class ReportDef:
    key: str
    label: str
    description: str
    builder: Callable
    filters: tuple[str, ...] = ()
    admin_only: bool = False


REPORTS: dict[str, ReportDef] = {r.key: r for r in (
    ReportDef("units", "تقرير الوحدات", "كل الوحدات بحالتها والمستأجر وتاريخ انتهاء العقد",
              units_report, ("site", "status")),
    ReportDef("properties", "تقرير العقارات", "كل عمارة بأعداد وحداتها حسب الحالة ونسبة الإشغال",
              properties_report, ("site",)),
    ReportDef("contracts", "تقرير العقود", "الوحدات المؤجرة وتواريخ انتهاء عقودها",
              contracts_report, ("site", "days")),
    ReportDef("users", "تقرير المستخدمين", "المستخدمون وأدوارهم والمواقع المسندة (للمدير)",
              users_report, (), admin_only=True),
)}

# تقارير مخططة لمراحل لاحقة تظهر في الواجهة معطّلة
UPCOMING = [{"key": "payments", "label": "تقرير الدفعات", "available": False,
             "note": "يتوفر مع وحدة العقود والدفعات في المرحلة 4"}]


def build_report(db: Session, key: str, *, site_id=None, status=None, days=None):
    return REPORTS[key].builder(db, site_id=site_id, status=status, days=days)
