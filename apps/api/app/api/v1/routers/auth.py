from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.core.security import create_token, decode_token, verify_password
from app.db.session import get_db
from app.deps import get_current_user
from app.models.models import User
from app.schemas.schemas import LoginRequest, RefreshRequest, TokenPair, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenPair)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenPair:
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
    db.commit()

    return TokenPair(
        access_token=create_token(str(user.id), "access"),
        refresh_token=create_token(str(user.id), "refresh"),
    )


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)) -> TokenPair:
    try:
        payload = decode_token(body.refresh_token)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")

    if payload.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not a refresh token")

    user = db.get(User, payload["sub"])
    if user is None or not user.enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or disabled")

    return TokenPair(
        access_token=create_token(str(user.id), "access"),
        refresh_token=create_token(str(user.id), "refresh"),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout() -> None:
    # Stateless JWTs: nothing to invalidate server-side yet. Milestone 8
    # scope is auth working end-to-end, not a token blocklist — revisit if
    # a real security review calls for one.
    return None


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
