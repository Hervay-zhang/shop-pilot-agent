from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User

bearer = HTTPBearer(auto_error=False)
ALGORITHM = "HS256"


def issue_demo_token(user: User) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user.id,
        "role": user.role,
        "name": user.name,
        "iat": now,
        "exp": now + timedelta(hours=12),
        "iss": "shop-pilot-demo",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
            issuer="shop-pilot-demo",
        )
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_INVALID", "message": "登录凭证无效或已过期"},
        ) from exc


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_REQUIRED", "message": "请先获取演示登录凭证"},
        )
    claims = decode_token(credentials.credentials)
    user = db.get(User, claims["sub"])
    if not user:
        raise HTTPException(status_code=401, detail={"code": "USER_NOT_FOUND"})
    return user


def get_sse_user(
    token: Annotated[str, Query()],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    claims = decode_token(token)
    user = db.get(User, claims["sub"])
    if not user:
        raise HTTPException(status_code=401, detail={"code": "USER_NOT_FOUND"})
    return user


def require_role(user: User, allowed: set[str]) -> None:
    if user.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ROLE_FORBIDDEN", "message": "当前角色无权执行该操作"},
        )


CurrentUser = Annotated[User, Depends(get_current_user)]
SSEUser = Annotated[User, Depends(get_sse_user)]
