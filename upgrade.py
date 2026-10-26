"""ترقية 3هـ: يضيف أعمدة المستويات إلى جدول المستخدمين دون مسّ البيانات (آمن للتكرار).
التشغيل اليدوي: docker compose exec api python -m app.upgrade
وهو يعمل تلقائياً أيضاً عند إقلاع الخادم."""
from sqlalchemy import inspect, text

COLUMNS = (("job_level", "VARCHAR(20)"), ("visibility_override", "VARCHAR(10)"))


def upgrade_schema(engine) -> list[str]:
    done: list[str] = []
    if "users" not in inspect(engine).get_table_names():
        return done
    have = {c["name"] for c in inspect(engine).get_columns("users")}
    with engine.begin() as conn:
        for name, ddl in COLUMNS:
            if name not in have:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {ddl}"))
                done.append(name)
        conn.execute(text("UPDATE users SET job_level='general_manager' WHERE job_level IS NULL AND role='admin'"))
        conn.execute(text("UPDATE users SET job_level='supervisor' WHERE job_level IS NULL AND role='updater'"))
        conn.execute(text("UPDATE users SET job_level='receptionist' WHERE job_level IS NULL AND role='viewer'"))
    return done


if __name__ == "__main__":
    from .database import Base, engine
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
    added = upgrade_schema(engine)
    print("أُضيفت الأعمدة:", ", ".join(added) if added else "لا شيء (محدّثة مسبقاً)")
