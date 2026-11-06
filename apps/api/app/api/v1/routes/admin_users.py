"""子账号管理接口。

只有 role=super 的账号可以调用这里的写接口。超级管理员本身不可被删除、
不可被降级，避免把系统锁死在无人可管的状态。
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.camel import CamelModel
from ....core.database import get_db
from ....core.response import ResultObject
from ....core.security import hash_password, validate_password_strength
from ....models.entities import XianyuOperationLog
from ....services import admin_user_service as svc
from ..deps import get_current_user, require_super

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/users", tags=["auth-users"])


class PermissionItem(CamelModel):
    resourceType: str
    resourceId: Optional[int] = None
    action: str = "read"


class CreateUserReq(CamelModel):
    username: str
    password: str
    role: str = "operator"
    email: Optional[str] = None
    accountLimit: Optional[int] = None
    permissions: list[PermissionItem] = []


class UpdateUserReq(CamelModel):
    email: Optional[str] = None
    role: Optional[str] = None
    status: Optional[int] = None
    accountLimit: Optional[int] = None


class ResetPasswordReq(CamelModel):
    newPassword: str


class PermissionsReq(CamelModel):
    permissions: list[PermissionItem] = []


def _public(row: dict) -> dict:
    """输出前剔除密码哈希与 TOTP 密钥。"""
    return {
        "id": row.get("id"),
        "username": row.get("username"),
        "email": row.get("email"),
        "role": svc.normalize_role(row.get("role")),
        "status": int(row.get("status") or 0),
        "accountLimit": row.get("account_limit"),
        "lastLoginTime": str(row["last_login_time"]) if row.get("last_login_time") else None,
        "lastLoginIp": row.get("last_login_ip"),
        "totpEnabled": bool(row.get("totp_enabled")),
        "createdTime": str(row["created_time"]) if row.get("created_time") else None,
    }


async def _audit(db: AsyncSession, operator: str, action: str, target: str) -> None:
    db.add(
        XianyuOperationLog(
            operator=operator,
            operation_type=action,
            operation_desc=f"子账号管理: {action}",
            target_type="admin_user",
            target_id=target,
        )
    )


@router.get("", response_model=ResultObject[list[dict]])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_super),
):
    rows = await svc.list_users(db)
    return ResultObject.success([_public(row) for row in rows])


@router.post("", response_model=ResultObject[dict])
async def create_user(
    req: CreateUserReq,
    db: AsyncSession = Depends(get_db),
    current: dict = Depends(require_super),
):
    username = (req.username or "").strip()
    if not username or not req.password:
        return ResultObject.validate_failed("用户名和密码不能为空")
    if len(username) < 3:
        return ResultObject.validate_failed("用户名至少 3 个字符")
    if await svc.username_exists(db, username):
        return ResultObject.validate_failed("用户名已存在")
    if req.email and await svc.email_exists(db, req.email):
        return ResultObject.validate_failed("邮箱已被占用")

    strength_error = validate_password_strength(req.password, username)
    if strength_error:
        return ResultObject.validate_failed(strength_error)

    role = svc.normalize_role(req.role)
    if role == svc.ROLE_SUPER:
        return ResultObject.validate_failed("不能创建第二个超级管理员")

    try:
        user_id = await svc.create_user(
            db,
            username=username,
            password_hash=hash_password(req.password),
            role=role,
            email=(req.email or None),
            account_limit=req.accountLimit,
            created_by=int(current.get("user_id") or 0) or None,
        )
        if req.permissions:
            await svc.replace_permissions(
                db, user_id, [p.model_dump() for p in req.permissions]
            )
        await _audit(db, current.get("username", "admin"), "create", str(user_id))
        await db.commit()
    except Exception:
        await db.rollback()
        logger.error("Create sub account failed", exc_info=True)
        return ResultObject.error("创建失败，请检查数据库")

    row = await svc.get_by_id(db, user_id)
    return ResultObject.success(_public(row or {}))


@router.put("/{user_id}", response_model=ResultObject[dict])
async def update_user(
    user_id: int,
    req: UpdateUserReq,
    db: AsyncSession = Depends(get_db),
    current: dict = Depends(require_super),
):
    row = await svc.get_by_id(db, user_id)
    if not row:
        return ResultObject.not_found("账号不存在")
    if svc.normalize_role(row.get("role")) == svc.ROLE_SUPER:
        return ResultObject.validate_failed("超级管理员不可修改")
    if req.email and await svc.email_exists(db, req.email, exclude_id=user_id):
        return ResultObject.validate_failed("邮箱已被占用")
    if req.role is not None and svc.normalize_role(req.role) == svc.ROLE_SUPER:
        return ResultObject.validate_failed("不能提升为超级管理员")

    try:
        await svc.update_user(
            db,
            user_id,
            email=req.email,
            role=req.role,
            status=req.status,
            account_limit=req.accountLimit,
        )
        await _audit(db, current.get("username", "admin"), "update", str(user_id))
        await db.commit()
    except Exception:
        await db.rollback()
        logger.error("Update sub account failed", exc_info=True)
        return ResultObject.error("更新失败")

    return ResultObject.success(_public(await svc.get_by_id(db, user_id) or {}))


@router.delete("/{user_id}", response_model=ResultObject[str])
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current: dict = Depends(require_super),
):
    row = await svc.get_by_id(db, user_id)
    if not row:
        return ResultObject.not_found("账号不存在")
    if svc.normalize_role(row.get("role")) == svc.ROLE_SUPER:
        return ResultObject.validate_failed("超级管理员不可删除")
    if int(current.get("user_id") or 0) == int(user_id):
        return ResultObject.validate_failed("不能删除自己")

    try:
        await svc.delete_user(db, user_id)
        await _audit(db, current.get("username", "admin"), "delete", str(user_id))
        await db.commit()
    except Exception:
        await db.rollback()
        logger.error("Delete sub account failed", exc_info=True)
        return ResultObject.error("删除失败")

    return ResultObject.success("已删除")


@router.put("/{user_id}/password", response_model=ResultObject[str])
async def reset_password(
    user_id: int,
    req: ResetPasswordReq,
    db: AsyncSession = Depends(get_db),
    current: dict = Depends(require_super),
):
    row = await svc.get_by_id(db, user_id)
    if not row:
        return ResultObject.not_found("账号不存在")
    strength_error = validate_password_strength(req.newPassword, row.get("username") or "")
    if strength_error:
        return ResultObject.validate_failed(strength_error)

    try:
        await svc.update_user(
            db, user_id, password_hash=hash_password(req.newPassword)
        )
        await _audit(db, current.get("username", "admin"), "reset_password", str(user_id))
        await db.commit()
    except Exception:
        await db.rollback()
        logger.error("Reset sub account password failed", exc_info=True)
        return ResultObject.error("重置失败")

    return ResultObject.success("密码已重置")


@router.get("/{user_id}/permissions", response_model=ResultObject[list[dict]])
async def get_permissions(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_super),
):
    return ResultObject.success(await svc.get_permissions(db, user_id))


@router.put("/{user_id}/permissions", response_model=ResultObject[list[dict]])
async def set_permissions(
    user_id: int,
    req: PermissionsReq,
    db: AsyncSession = Depends(get_db),
    current: dict = Depends(require_super),
):
    row = await svc.get_by_id(db, user_id)
    if not row:
        return ResultObject.not_found("账号不存在")
    try:
        await svc.replace_permissions(
            db, user_id, [p.model_dump() for p in req.permissions]
        )
        await _audit(db, current.get("username", "admin"), "set_permissions", str(user_id))
        await db.commit()
    except Exception:
        await db.rollback()
        logger.error("Set permissions failed", exc_info=True)
        return ResultObject.error("保存失败")
    return ResultObject.success(await svc.get_permissions(db, user_id))


@router.get("/me/permissions", response_model=ResultObject[list[dict]])
async def my_permissions(
    db: AsyncSession = Depends(get_db),
    current: dict = Depends(get_current_user),
):
    if svc.normalize_role(current.get("role")) == svc.ROLE_SUPER:
        return ResultObject.success([{"resourceType": "*", "resourceId": None, "action": "write"}])
    return ResultObject.success(await svc.get_permissions(db, int(current.get("user_id") or 0)))


@router.get("/me/profile", response_model=ResultObject[dict])
async def my_profile(
    db: AsyncSession = Depends(get_db),
    current: dict = Depends(get_current_user),
):
    user_id = int(current.get("user_id") or 0)
    row = await svc.get_by_id(db, user_id) if user_id else None
    if not row:
        # 尚未迁移到表里的历史会话，按超管返回配置值
        return ResultObject.success(
            {
                "id": 0,
                "username": current.get("username"),
                "role": svc.ROLE_SUPER,
                "accountLimit": None,
                "totpEnabled": False,
            }
        )
    return ResultObject.success(_public(row))
