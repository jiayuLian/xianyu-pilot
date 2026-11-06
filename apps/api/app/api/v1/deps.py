from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.redis_client import RedisUnavailableError
from app.core.security import authenticate_token

bearer_scheme = HTTPBearer(auto_error=False)


def normalize_role(value) -> str:
    """把 token 里的历史角色写法收敛到 super / operator / viewer。

    旧版本所有 token 的 role 都是 "admin"，升级后必须继续按超级管理员处理，
    否则存量会话会在下一次请求时全部 403。
    """
    raw = str(value or "").strip().casefold()
    if raw in ("super", "admin", "root"):
        return "super"
    if raw in ("viewer", "readonly", "read"):
        return "viewer"
    return "operator"


async def _decode_admin_credentials(
    credentials: HTTPAuthorizationCredentials | None,
) -> dict | None:
    if not credentials or credentials.scheme.lower() != "bearer":
        return None
    try:
        payload = await authenticate_token(credentials.credentials)
    except RedisUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="认证安全状态暂不可用，请稍后重试",
        ) from exc
    if payload is None:
        return None
    return {
        "user_id": int(payload.get("uid") or 0),
        "username": payload.get("username") or "",
        "role": normalize_role(payload.get("role")),
        "jti": payload["jti"],
        "exp": payload["exp"],
    }


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    # Authentication middleware stores the already-validated user to avoid a
    # second Redis revocation lookup on normal API requests.
    user = getattr(request.state, "current_user", None)
    if user is None:
        user = await _decode_admin_credentials(credentials)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="暂未登录或登录已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if "role" in user:
        user = {**user, "role": normalize_role(user.get("role"))}
    return user


async def get_current_user_optional(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict | None:
    user = getattr(request.state, "current_user", None)
    if user is not None:
        return user
    return await _decode_admin_credentials(credentials)


async def require_super(user: dict = Depends(get_current_user)) -> dict:
    if normalize_role(user.get("role")) != "super":
        raise HTTPException(status_code=403, detail="需要超级管理员权限")
    return user


def require_role(*allowed: str):
    """构造一个只放行指定角色的依赖，用于路由级鉴权。"""
    allowed_set = {normalize_role(item) for item in allowed}

    async def _dependency(user: dict = Depends(get_current_user)) -> dict:
        if normalize_role(user.get("role")) not in allowed_set:
            raise HTTPException(status_code=403, detail="权限不足")
        return user

    return _dependency
