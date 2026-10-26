from sqlalchemy.orm import Session

from .models import AuditLog, User


def audit(db: Session, user: User | None, action: str, entity: str,
          entity_id: int | None = None, detail: str | None = None) -> None:
    """يضيف سجلاً إلى audit_log؛ الـ commit مسؤولية المستدعي."""
    db.add(AuditLog(user_id=user.id if user else None, action=action,
                    entity=entity, entity_id=entity_id, detail=detail))
