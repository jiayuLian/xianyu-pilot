import asyncio
import logging
import hmac
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.camel import CamelModel
from ....core.config import settings
from ....core.database import get_db
from ....core.response import ResultObject
from ....core.redis_client import RedisUnavailableError
from ....core.security import (
    clear_login_failures,
    create_token,
    hash_password,
    login_retry_after,
    record_login_failure,
    request_client_ip,
    revoke_all_tokens,
    revoke_token_payload,
    validate_password_strength,
    verify_password,
)
from ....models.entities import XianyuOperationLog, XianyuSysSetting
from ....services import admin_user_service
from ..deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

PASSWORD_SETTING_KEY = "admin_password_hash"
LAST_LOGIN_SETTING_KEY = "admin_last_login_time"
LAST_SECURITY_UPDATE_SETTING_KEY = "admin_last_security_update_time"
ADMIN_PHONE_SETTING_KEY = "admin_phone"
ADMIN_PHONE_VERIFIED_SETTING_KEY = "admin_phone_verified"
ADMIN_EMAIL_SETTING_KEY = "admin_email"
ADMIN_EMAIL_VERIFIED_SETTING_KEY = "admin_email_verified"


class LoginReqDTO(CamelModel):
    username: str
    password: str


class LoginRespDTO(CamelModel):
    token: str
    username: str
    role: str = "admin"


class ProfileRespDTO(CamelModel):
    user_id: int = 0
    username: str
    role: str = "admin"
    avatar: Optional[str] = ""
    email: Optional[str] = ""


class ChangePasswordReqDTO(CamelModel):
    old_password: str
    new_password: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def load_setting_value(db: AsyncSession, key: str, default: str = "") -> str:
    result = await db.execute(
        select(XianyuSysSetting).where(XianyuSysSetting.setting_key == key)
    )
    setting = result.scalar_one_or_none()
    if setting and setting.setting_value is not None:
        return setting.setting_value
    return default


async def save_setting_value(
    db: AsyncSession,
    key: str,
    value: str,
    *,
    commit: bool = True,
) -> None:
    result = await db.execute(
        select(XianyuSysSetting).where(XianyuSysSetting.setting_key == key)
    )
    setting = result.scalar_one_or_none()
    if setting:
        setting.setting_value = value
    else:
        db.add(XianyuSysSetting(setting_key=key, setting_value=value))
    if commit:
        await db.commit()


async def load_admin_password_hash(db: AsyncSession) -> str:
    try:
        stored = await load_setting_value(db, PASSWORD_SETTING_KEY, "")
        if stored:
            return stored
    except Exception:
        if (settings.app_env or "").strip().casefold() != "test":
            logger.error("Failed to load admin password hash from database", exc_info=True)
            raise
        logger.warning("Failed to load admin password hash from db, fallback to test env", exc_info=True)
    return settings.admin_password_hash


async def save_admin_password_hash(
    db: AsyncSession,
    new_hash: str,
    *,
    commit: bool = True,
) -> None:
    await save_setting_value(db, PASSWORD_SETTING_KEY, new_hash, commit=commit)


async def mark_admin_login(db: AsyncSession, *, commit: bool = True) -> None:
    await save_setting_value(
        db,
        LAST_LOGIN_SETTING_KEY,
        now_iso(),
        commit=commit,
    )


async def mark_admin_security_update(db: AsyncSession, *, commit: bool = True) -> None:
    await save_setting_value(
        db,
        LAST_SECURITY_UPDATE_SETTING_KEY,
        now_iso(),
        commit=commit,
    )


async def validate_admin_credentials(
    db: AsyncSession, username: str, password: str
) -> tuple[Optional[dict], Optional[str]]:
    """校验账号密码，返回 (用户记录, 错误信息)。

    优先查 admin_user 表（支持子账号）。表里没有该用户名时回退到 .env 的
    单管理员配置，兼容尚未执行 046 迁移的旧库和首次启动场景。
    """
    username = (username or "").strip()
    password = password or ""
    if not username or not password:
        return None, "用户名或密码不能为空"

    user = await admin_user_service.get_by_username(db, username)
    if user:
        if not int(user.get("status") or 0):
            return None, "账号已被停用，请联系管理员"
        remaining = admin_user_service.lock_remaining_seconds(user)
        if remaining > 0:
            return None, f"账号已锁定，请 {remaining // 60 + 1} 分钟后重试"

        matched = await asyncio.to_thread(
            verify_password, password, user.get("password_hash") or ""
        )
        if not matched:
            attempts = await admin_user_service.record_failed_login(db, int(user["id"]))
            await db.commit()
            if attempts >= admin_user_service.DEFAULT_MAX_FAILED_ATTEMPTS:
                return None, "密码错误次数过多，账号已锁定"
            return None, "用户名或密码错误"

        # 登录成功清零失败计数并记录来源 IP，由调用方负责 commit
        user["password_hash"] = ""
        return user, None

    password_hash = await load_admin_password_hash(db)
    if not password_hash:
        return None, "管理员密码未配置，请在 .env 中设置 ADMIN_PASSWORD_HASH"
    username_matches = hmac.compare_digest(username, settings.admin_username)
    password_matches = await asyncio.to_thread(
        verify_password,
        password,
        password_hash,
    )
    if not username_matches or not password_matches:
        return None, "用户名或密码错误"
    return (
        {
            "id": 0,
            "username": username,
            "role": admin_user_service.ROLE_SUPER,
            "status": 1,
            "account_limit": None,
        },
        None,
    )


async def update_admin_password(
    db: AsyncSession,
    *,
    old_password: str,
    new_password: str,
    operator: str,
    ip_address: str,
    operation_desc: str,
    target_type: str,
) -> str | None:
    """Apply the single password-change policy used by every compatibility API."""
    old_password = old_password or ""
    new_password = new_password or ""
    if not old_password or not new_password:
        return "当前密码和新密码不能为空"

    strength_error = validate_password_strength(new_password, settings.admin_username)
    if strength_error:
        return strength_error

    stored_hash = await load_admin_password_hash(db)
    if not stored_hash:
        return "管理员密码尚未配置"
    if not await asyncio.to_thread(verify_password, old_password, stored_hash):
        return "当前密码错误"
    if await asyncio.to_thread(verify_password, new_password, stored_hash):
        return "新密码不能与当前密码相同"

    try:
        new_password_hash = await asyncio.to_thread(hash_password, new_password)
        await save_admin_password_hash(db, new_password_hash, commit=False)
        await mark_admin_security_update(db, commit=False)
        db.add(XianyuOperationLog(
            operator=operator,
            operation_type="change_password",
            operation_desc=operation_desc,
            target_type=target_type,
            target_id="admin",
            ip_address=ip_address,
        ))
        await revoke_all_tokens()
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return None


async def enforce_login_rate_limit(request: Request) -> None:
    retry_after = await login_retry_after(request)
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="登录尝试次数过多，请稍后重试",
            headers={"Retry-After": str(retry_after)},
        )


@router.post("/login", response_model=ResultObject[LoginRespDTO])
async def login(req: LoginReqDTO, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        await enforce_login_rate_limit(request)
        user, error = await validate_admin_credentials(db, req.username, req.password)
        if error:
            if error == "用户名或密码不能为空":
                raise HTTPException(status_code=422, detail=error)
            if error.startswith("管理员密码未配置"):
                raise HTTPException(status_code=503, detail="认证服务尚未完成管理员密码配置")
            if error.startswith("账号已"):
                # 停用与锁定属于不可重试状态，直接 403，避免前端反复提交
                raise HTTPException(status_code=403, detail=error)
            await record_login_failure(request)
            raise HTTPException(status_code=401, detail="用户名或密码错误")

        await clear_login_failures(request)
        account = user or {}
        token_username = account.get("username") or settings.admin_username
        role_value = admin_user_service.normalize_role(account.get("role"))
        account_id = int(account.get("id") or 0)
        token = create_token(token_username, user_id=account_id, role=role_value)
        try:
            if account_id:
                await admin_user_service.mark_login_success(
                    db, account_id, request_client_ip(request)
                )
            else:
                await mark_admin_login(db, commit=False)
            db.add(XianyuOperationLog(
                operator=token_username,
                operation_type="login",
                operation_desc="管理员登录",
                target_type="auth",
                target_id="admin",
                ip_address=request_client_ip(request),
            ))
            await db.commit()
        except Exception:
            await db.rollback()
            logger.error("Failed to persist successful-login audit", exc_info=True)
            raise HTTPException(
                status_code=503,
                detail="登录审计暂不可用，未签发会话",
            )

        return ResultObject.success(
            LoginRespDTO(token=token, username=token_username, role=role_value)
        )
    except RedisUnavailableError:
        raise HTTPException(
            status_code=503,
            detail="认证安全状态暂不可用，请稍后重试",
        )
    except HTTPException:
        raise
    except Exception:
        logger.error("管理员登录失败", exc_info=True)
        raise HTTPException(status_code=503, detail="登录服务暂不可用，请稍后重试")


@router.get("/profile", response_model=ResultObject[ProfileRespDTO])
async def get_profile(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    email = await load_setting_value(db, ADMIN_EMAIL_SETTING_KEY, "")
    return ResultObject.success(ProfileRespDTO(
        user_id=current_user.get("user_id", 0),
        username=current_user.get("username", settings.admin_username),
        role=current_user.get("role", "admin"),
        email=email,
    ))


@router.post("/logout", response_model=ResultObject[None])
async def logout(current_user: dict = Depends(get_current_user)):
    try:
        await revoke_token_payload(current_user)
    except RedisUnavailableError:
        raise HTTPException(
            status_code=503,
            detail="认证安全状态暂不可用，退出结果未确认，请稍后重试",
        )
    return ResultObject.success(None, message="已退出登录")


@router.put("/password", response_model=ResultObject[str])
async def change_password(
    req: ChangePasswordReqDTO,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        error = await update_admin_password(
            db,
            old_password=req.old_password,
            new_password=req.new_password,
            operator=current_user.get("username", settings.admin_username),
            ip_address=request_client_ip(request),
            operation_desc="管理员修改登录密码",
            target_type="auth",
        )
        if error:
            return ResultObject.validate_failed(error)

        return ResultObject.success("密码修改成功")
    except RedisUnavailableError:
        raise HTTPException(
            status_code=503,
            detail="认证安全状态暂不可用，密码未修改，请稍后重试",
        )
    except Exception:
        logger.error("修改密码失败", exc_info=True)
        return ResultObject.failed("修改密码失败，请稍后重试")
