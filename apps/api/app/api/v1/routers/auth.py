from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.core.config import settings
from app.core.security import (
    create_refresh_secret,
    create_token,
    hash_refresh_secret,
    verify_password,
)
from app.db.session import get_db
from app.deps import get_current_user
from app.models.models import RefreshSession, User
from app.schemas.schemas import LoginRequest, TokenPair, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_refresh_cookie(response: Response, secret: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=secret,
        max_age=settings.refresh_token_expire_minutes * 60,
        httponly=True,
        secure=settings.environment == "production",
        samesite=settings.refresh_cookie_samesite,
        path="/api/v1/auth",
    )


def _new_refresh_session(db: Session, user_id) -> str:
    secret = create_refresh_secret()
    db.add(
        RefreshSession(
            user_id=user_id,
            token_hash=hash_refresh_secret(secret),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.refresh_token_expire_minutes),
        )
    )
    return secret


@router.post("/login", response_model=TokenPair)
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)) -> TokenPair:
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not user.enabled or not verify_password(body.password, user.password_hash):
        # Milestone 17 (audit verification): a failed login attempt is
        # security-relevant too, but there's no authenticated actor to
        # attach it to and no user_id should be leaked back to a caller
        # who got the username wrong — record it against no actor,
        # keyed by the attempted username instead.
        record_audit(
            db,
            actor_user_id=None,
            action="auth.login.failed",
            resource_type="user",
            resource_id=body.username,
            metadata={},
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect username or password")

    user.last_login_at = datetime.now(timezone.utc)
    record_audit(
        db,
        actor_user_id=user.id,
        action="auth.login",
        resource_type="user",
        resource_id=str(user.id),
        metadata={},
    )
    refresh_secret = _new_refresh_session(db, user.id)
    db.commit()
    _set_refresh_cookie(response, refresh_secret)

    return TokenPair(access_token=create_token(str(user.id), "access"))


@router.post("/refresh", response_model=TokenPair)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> TokenPair:
    secret = request.cookies.get(settings.refresh_cookie_name)
    if not secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh session is required")
    session = db.scalar(
        select(RefreshSession)
        .where(RefreshSession.token_hash == hash_refresh_secret(secret))
        .with_for_update()
    )
    now = datetime.now(timezone.utc)
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh session")
    user = db.get(User, session.user_id)
    if user is None or not user.enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or disabled")
    session.revoked_at = now
    replacement = _new_refresh_session(db, user.id)
    db.commit()
    _set_refresh_cookie(response, replacement)
    return TokenPair(access_token=create_token(str(user.id), "access"))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    secret = request.cookies.get(settings.refresh_cookie_name)
    if secret:
        session = db.scalar(
            select(RefreshSession).where(RefreshSession.token_hash == hash_refresh_secret(secret))
        )
        if session is not None and session.revoked_at is None:
            session.revoked_at = datetime.now(timezone.utc)
            db.commit()
    response.delete_cookie(settings.refresh_cookie_name, path="/api/v1/auth")


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> UserOut:
    return UserOut(
        **{
            "id": current_user.id,
            "username": current_user.username,
            "display_name": current_user.display_name,
            "email": current_user.email,
            "enabled": current_user.enabled,
            "last_login_at": current_user.last_login_at,
            "roles": [r.name for r in current_user.roles],
            "permissions": sorted(current_user.permission_names()),
        }
    )
