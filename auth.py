from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..access import user_caps
from ..schemas import ChangePassword, MeOut, TokenOut
from ..security import create_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == form.username.strip().lower()))
    if not user or not user.is_active or not verify_password(form.password, user.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "اسم المستخدم أو كلمة المرور غير صحيحة",
            headers={"WWW-Authenticate": "Bearer"},
        )
    audit(db, user, "login", "user", user.id)
    db.commit()
    return TokenOut(access_token=create_token(user.id))


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    out = MeOut.model_validate(user)
    out.caps = sorted(user_caps(db, user))
    return out


@router.post("/change-password", status_code=204)
def change_password(body: ChangePassword, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "كلمة المرور الحالية غير صحيحة")
    user.password_hash = hash_password(body.new_password)
    audit(db, user, "change_password", "user", user.id)
    db.commit()
