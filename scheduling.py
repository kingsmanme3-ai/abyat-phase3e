"""حساب موعد التشغيل التالي للتقارير المجدولة (دالة نقية بلا اعتماد على قاعدة البيانات)."""
from datetime import date, datetime, timedelta, timezone

WEEKDAYS_AR = ["الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"]


def compute_next_run(frequency: str, hour: int, weekday: int | None, day_of_month: int | None,
                     after: datetime, tz_hours: float) -> datetime:
    """أول موعد تشغيل بعد `after` (ساعة محلية كاملة)، يُعاد بتوقيت UTC.

    - weekday: 0=الأحد ... 6=السبت (بداية الأسبوع السعودية).
    - day_of_month: من 1 إلى 28 حتى يوجد في كل الشهور.
    """
    tz = timezone(timedelta(hours=tz_hours))
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    now = after.astimezone(tz)

    def at(d: date) -> datetime:
        return datetime(d.year, d.month, d.day, hour, 0, tzinfo=tz)

    d0 = now.date()
    if frequency == "daily":
        c = at(d0)
        if c <= now:
            c = at(d0 + timedelta(days=1))
    elif frequency == "weekly":
        if weekday is None:
            raise ValueError("weekday مطلوب للجدولة الأسبوعية")
        target = (weekday - 1) % 7            # تحويل الأحد=0 إلى weekday() في بايثون (الاثنين=0)
        c = at(d0 + timedelta(days=(target - d0.weekday()) % 7))
        if c <= now:
            c = at(c.date() + timedelta(days=7))
    elif frequency == "monthly":
        dom = min(max(day_of_month or 1, 1), 28)
        c = at(date(d0.year, d0.month, dom))
        if c <= now:
            y, m = d0.year + (d0.month == 12), d0.month % 12 + 1
            c = at(date(y, m, dom))
    else:
        raise ValueError(f"تكرار غير معروف: {frequency}")
    return c.astimezone(timezone.utc)
