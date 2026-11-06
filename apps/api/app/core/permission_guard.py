"""子账号写权限守卫中间件。

RBAC 只写进 admin_user_permission 表不会产生任何效果——必须先拦在请求上。
这里按 URL 前缀把请求映射到资源类型，写方法要求该资源对应的 write 权限。

设计取舍：
- 只拦写方法（POST/PUT/PATCH/DELETE）。读请求放行，避免只读账号连页面都
  加载不出来，也避免把权限模型变成另一个需要维护的读拦截矩阵。
- 无法映射到资源类型的路径一律放行。宁可漏拦也不要误伤后台自身接口，
  漏拦的部分由各路由自己的 require_super / require_role 兜底。
- super 直接放行，不做任何查询。
- 权限查询失败按「无权限」处理，但不返回 500，避免把 RBAC 故障放大成
  整个后台不可用。
"""

import logging
from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.database import async_session
from app.core.redis_client import redis_get, redis_set
from app.services import admin_user_service

logger = logging.getLogger(__name__)

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
READ_METHODS = {"GET", "HEAD", "OPTIONS"}

# 前缀映射：长前缀优先匹配，避免 /api/accounts 被 /api/account 抢先命中
RESOURCE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/api/products", "goods"),
    ("/api/product", "goods"),
    ("/api/item", "goods"),
    ("/api/xianyu/accounts", "account"),
    ("/api/xianyu/categories", "goods"),
    ("/api/accounts", "account"),
    ("/api/account", "account"),
    ("/api/delivery", "delivery"),
    ("/api/card", "delivery"),
    ("/api/kami", "delivery"),
    ("/api/auto-reply", "reply"),
    ("/api/reply", "reply"),
    ("/api/quick-reply", "reply"),
)

# 这些前缀属于认证与自助接口，必须对任何已登录账号开放
EXEMPT_PREFIXES = (
    "/api/auth",
    "/api/profile",
    "/api/notifications",
    "/api/dashboard",
    "/api/navigation",
    "/api/system",
    "/api/client-errors",
    "/api/internal",
    "/api/captcha",
)

_PERMISSION_CACHE_PREFIX = "rbac:perm:"
_PERMISSION_CACHE_TTL = 60


def resolve_resource(path: str) -> Optional[str]:
    for prefix, resource in RESOURCE_PREFIXES:
        if path.startswith(prefix):
            return resource
    return None


def is_exempt(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES)


async def _load_permissions(user_id: int) -> list[dict]:
    cache_key = f"{_PERMISSION_CACHE_PREFIX}{user_id}"
    try:
        cached = await redis_get(cache_key)
        if cached:
            import json

            return json.loads(cached)
    except Exception:
        pass

    async with async_session() as db:
        permissions = await admin_user_service.get_permissions(db, user_id)

    try:
        import json

        await redis_set(cache_key, json.dumps(permissions), ex=_PERMISSION_CACHE_TTL)
    except Exception:
        pass
    return permissions


def invalidate_permission_cache(user_id: int) -> None:
    """权限变更后调用，避免最长 60 秒的旧权限生效窗口。"""
    import asyncio

    async def _drop() -> None:
        try:
            from app.core.redis_client import redis_delete

            await redis_delete(f"{_PERMISSION_CACHE_PREFIX}{user_id}")
        except Exception:
            logger.debug("Drop permission cache failed userId=%s", user_id, exc_info=True)

    try:
        asyncio.get_event_loop().create_task(_drop())
    except RuntimeError:
        pass


class PermissionGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method.upper()

        if method in READ_METHODS or not path.startswith("/api/"):
            return await call_next(request)
        if method not in WRITE_METHODS or is_exempt(path):
            return await call_next(request)

        user = getattr(request.state, "current_user", None)
        # 未带登录态的请求交给路由自己返回 401，这里不越权判断
        if not user:
            return await call_next(request)

        role = str(user.get("role") or "").strip().casefold()
        if role in ("super", "admin", "root"):
            return await call_next(request)
        if role in ("viewer", "readonly", "read"):
            return JSONResponse(
                status_code=403,
                content={"code": 403, "msg": "只读账号不能执行修改操作", "data": None},
            )

        resource = resolve_resource(path)
        if resource is None:
            return await call_next(request)

        user_id = int(user.get("user_id") or 0)
        if not user_id:
            return JSONResponse(
                status_code=403,
                content={"code": 403, "msg": "账号信息不完整，请重新登录", "data": None},
            )

        try:
            permissions = await _load_permissions(user_id)
        except Exception:
            logger.warning("Load permissions failed userId=%s", user_id, exc_info=True)
            permissions = []

        if not admin_user_service.has_permission({"role": role}, permissions, resource, "write"):
            return JSONResponse(
                status_code=403,
                content={
                    "code": 403,
                    "msg": f"没有修改「{resource}」的权限，请联系超级管理员分配",
                    "data": None,
                },
            )

        return await call_next(request)
