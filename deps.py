from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .access import user_caps
from .database import get_db
from .models import Role, User
from .security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    user_id = decode_token(token)
    user = db.get(User, user_id) if user_id else None
    if not user or not user.is_active:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "الجلسة غير صالحة، سجّل الدخول من جديد",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "هذه العملية للمدير فقط")
    return user


def ensure_can_update_site(user: User, site_id: int) -> None:
    """المدير يحدّث كل المواقع، والمحدّث مواقعه المسندة فقط، والمشاهد لا يحدّث."""
    if user.role == Role.admin:
        return
    if user.role == Role.updater and site_id in user.site_ids:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "لا تملك صلاحية التحديث على هذا الموقع")


def require_cap(cap: str):
    """تبعية تتحقق من صلاحية في مصفوفة المستويات (الإداريون يملكونها دائماً)."""
    def dep(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        if cap not in user_caps(db, user):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "لا تملك صلاحية الوصول إلى هذه الميزة")
        return user
    return dep
