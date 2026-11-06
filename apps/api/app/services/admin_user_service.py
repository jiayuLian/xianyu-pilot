"""管理员与子账号的用户域服务。

登录链路自 2026-07 版本起只比对 ``.env`` 的 ADMIN_USERNAME 与单个
admin_password_hash，``admin_user`` 表虽然存在但从未被读取。这里把该表
接入认证链路，并补齐子账号所需的角色、配额、锁定与 TOTP 字段。

角色语义：
    super     超级管理员，全局唯一，不受权限表限制
    operator  操作员，按权限表操作资源
    viewer    只读，仅允许 read 动作

历史 token 里的 role 是字符串 "admin"，``normalize_role`` 会把它们统一
映射成 super，保证升级后旧会话不会因角色判断失效而全部踢下线。
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

ROLE_SUPER = "super"
ROLE_OPERATOR = "operator"
ROLE_VIEWER = "viewer"
VALID_ROLES = (ROLE_SUPER, ROLE_OPERATOR, ROLE_VIEWER)

# 与 .env 的 LOGIN_MAX_ATTEMPTS / LOGIN_LOCK_MINUTES 保持一致的默认值。
# 这里做账号级锁定，.env 那两个参数是 IP 级限速，两者互补。
DEFAULT_MAX_FAILED_ATTEMPTS = 5
DEFAULT_LOCK_MINUTES = 15

_USER_COLUMNS = (
    "id, username, email, password_hash, is_super, status, role, "
    "account_limit, created_by, last_login_time, last_login_ip, "
    "failed_attempts, locked_until, totp_secret, totp_enabled, "
    "created_time, updated_time"
)


def normalize_role(value: Any) -> str:
    """把任意历史写法收敛到三种角色之一。"""
    raw = str(value or "").strip().casefold()
    if raw in (ROLE_SUPER, "admin", "root"):
        return ROLE_SUPER
    if raw in (ROLE_VIEWER, "readonly", "read"):
        return ROLE_VIEWER
    return ROLE_OPERATOR


def _row_to_dict(row: Any) -> Optional[dict]:
    return dict(row) if row else None


async def get_by_username(db: AsyncSession, username: str) -> Optional[dict]:
    row = (
        await db.execute(
            text(f"SELECT {_USER_COLUMNS} FROM admin_user WHERE username = :username LIMIT 1"),
            {"username": (username or "").strip()},
        )
    ).mappings().first()
    return _row_to_dict(row)


async def get_by_id(db: AsyncSession, user_id: int) -> Optional[dict]:
    row = (
        await db.execute(
            text(f"SELECT {_USER_COLUMNS} FROM admin_user WHERE id = :user_id LIMIT 1"),
            {"user_id": int(user_id)},
        )
    ).mappings().first()
    return _row_to_dict(row)


async def list_users(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            text(
                "SELECT id, username, email, is_super, status, role, account_limit, "
                "created_by, last_login_time, last_login_ip, totp_enabled, created_time "
                "FROM admin_user ORDER BY id ASC"
            )
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def count_users(db: AsyncSession) -> int:
    value = (
        await db.execute(text("SELECT COUNT(*) FROM admin_user"))
    ).scalar_one_or_none()
    return int(value or 0)


async def username_exists(db: AsyncSession, username: str) -> bool:
    value = (
        await db.execute(
            text("SELECT 1 FROM admin_user WHERE username = :username LIMIT 1"),
            {"username": (username or "").strip()},
        )
    ).scalar_one_or_none()
    return value is not None


async def email_exists(db: AsyncSession, email: str, exclude_id: int = 0) -> bool:
    if not email:
        return False
    value = (
        await db.execute(
            text(
                "SELECT 1 FROM admin_user WHERE email = :email AND id <> :exclude LIMIT 1"
            ),
            {"email": email, "exclude": int(exclude_id)},
        )
    ).scalar_one_or_none()
    return value is not None


async def create_user(
    db: AsyncSession,
    *,
    username: str,
    password_hash: str,
    role: str = ROLE_OPERATOR,
    email: Optional[str] = None,
    account_limit: Optional[int] = None,
    created_by: Optional[int] = None,
) -> int:
    normalized_role = normalize_role(role)
    result = await db.execute(
        text(
            "INSERT INTO admin_user "
            "(username, email, password_hash, is_super, status, role, account_limit, "
            " created_by, failed_attempts, totp_enabled, created_time, updated_time) "
            "VALUES (:username, :email, :password_hash, :is_super, 1, :role, :account_limit, "
            "        :created_by, 0, 0, NOW(), NOW())"
        ),
        {
            "username": username.strip(),
            "email": (email or None),
            "password_hash": password_hash,
            "is_super": 1 if normalized_role == ROLE_SUPER else 0,
            "role": normalized_role,
            "account_limit": account_limit,
            "created_by": created_by,
        },
    )
    return int(result.lastrowid or 0)


async def update_user(
    db: AsyncSession,
    user_id: int,
    *,
    email: Optional[str] = None,
    role: Optional[str] = None,
    status: Optional[int] = None,
    account_limit: Optional[int] = None,
    password_hash: Optional[str] = None,
) -> None:
    sets: list[str] = []
    params: dict[str, Any] = {"user_id": int(user_id)}

    if email is not None:
        sets.append("email = :email")
        params["email"] = email or None
    if role is not None:
        normalized = normalize_role(role)
        sets.append("role = :role")
        sets.append("is_super = :is_super")
        params["role"] = normalized
        params["is_super"] = 1 if normalized == ROLE_SUPER else 0
    if status is not None:
        sets.append("status = :status")
        params["status"] = 1 if status else 0
    if account_limit is not None:
        sets.append("account_limit = :account_limit")
        params["account_limit"] = account_limit
    if password_hash:
        sets.append("password_hash = :password_hash")
        sets.append("failed_attempts = 0")
        sets.append("locked_until = NULL")
        params["password_hash"] = password_hash

    if not sets:
        return
    sets.append("updated_time = NOW()")
    await db.execute(
        text(f"UPDATE admin_user SET {', '.join(sets)} WHERE id = :user_id"), params
    )


async def delete_user(db: AsyncSession, user_id: int) -> None:
    await db.execute(
        text("DELETE FROM admin_user_permission WHERE user_id = :user_id"),
        {"user_id": int(user_id)},
    )
    await db.execute(
        text("DELETE FROM admin_user WHERE id = :user_id"), {"user_id": int(user_id)}
    )


def lock_remaining_seconds(user: dict) -> int:
    """返回账号剩余锁定秒数，0 表示未锁定。"""
    locked_until = user.get("locked_until")
    if not locked_until:
        return 0
    if isinstance(locked_until, str):
        try:
            locked_until = datetime.strptime(locked_until, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return 0
    remaining = (locked_until - datetime.now()).total_seconds()
    return max(0, int(remaining))


async def record_failed_login(
    db: AsyncSession, user_id: int, max_attempts: int = DEFAULT_MAX_FAILED_ATTEMPTS,
    lock_minutes: int = DEFAULT_LOCK_MINUTES,
) -> int:
    """累加失败次数，达到阈值时写锁定时间。返回当前失败次数。"""
    row = (
        await db.execute(
            text("SELECT failed_attempts FROM admin_user WHERE id = :user_id"),
            {"user_id": int(user_id)},
        )
    ).scalar_one_or_none()
    attempts = int(row or 0) + 1

    if attempts >= max_attempts:
        await db.execute(
            text(
                "UPDATE admin_user SET failed_attempts = :attempts, "
                "locked_until = DATE_ADD(NOW(), INTERVAL :minutes MINUTE), "
                "updated_time = NOW() WHERE id = :user_id"
            ),
            {"attempts": attempts, "minutes": int(lock_minutes), "user_id": int(user_id)},
        )
    else:
        await db.execute(
            text(
                "UPDATE admin_user SET failed_attempts = :attempts, updated_time = NOW() "
                "WHERE id = :user_id"
            ),
            {"attempts": attempts, "user_id": int(user_id)},
        )
    return attempts


async def clear_failed_login(db: AsyncSession, user_id: int) -> None:
    await db.execute(
        text(
            "UPDATE admin_user SET failed_attempts = 0, locked_until = NULL, "
            "updated_time = NOW() WHERE id = :user_id"
        ),
        {"user_id": int(user_id)},
    )


async def mark_login_success(db: AsyncSession, user_id: int, ip: str) -> None:
    await db.execute(
        text(
            "UPDATE admin_user SET failed_attempts = 0, locked_until = NULL, "
            "last_login_time = NOW(), last_login_ip = :ip, updated_time = NOW() "
            "WHERE id = :user_id"
        ),
        {"user_id": int(user_id), "ip": (ip or "")[:64]},
    )


async def ensure_super_admin(
    db: AsyncSession,
    *,
    username: str,
    password_hash: str,
) -> Optional[int]:
    """首次启动时把 .env 配置的管理员种入表。

    表里已有任何账号时不写入，避免覆盖线上改过的密码。
    """
    if not username or not password_hash:
        return None
    if await count_users(db) > 0:
        return None
    user_id = await create_user(
        db,
        username=username,
        password_hash=password_hash,
        role=ROLE_SUPER,
        account_limit=None,
        created_by=None,
    )
    logger.info("Seeded super admin %s (id=%s) from environment", username, user_id)
    return user_id


async def get_permissions(db: AsyncSession, user_id: int) -> list[dict]:
    rows = (
        await db.execute(
            text(
                "SELECT id, resource_type, resource_id, action "
                "FROM admin_user_permission WHERE user_id = :user_id ORDER BY id"
            ),
            {"user_id": int(user_id)},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def replace_permissions(
    db: AsyncSession, user_id: int, permissions: Iterable[dict]
) -> None:
    """整体替换某用户的权限集合。

    入参先去重：唯一索引 uk_admin_user_permission 里的 resource_id 允许 NULL，
    而 MySQL 的唯一索引对 NULL 不做冲突判定，同一条「全量」权限（resource_id
    为 NULL）重复提交会插入多行。这里在写库前收敛，保证幂等。
    """
    await db.execute(
        text("DELETE FROM admin_user_permission WHERE user_id = :user_id"),
        {"user_id": int(user_id)},
    )
    seen: set[tuple[str, Optional[int], str]] = set()
    for item in permissions or []:
        resource_type = str(item.get("resourceType") or item.get("resource_type") or "").strip()
        action = str(item.get("action") or "").strip().lower()
        if not resource_type or action not in ("read", "write"):
            continue
        raw_id = item.get("resourceId", item.get("resource_id"))
        resource_id = None
        if raw_id not in (None, "", 0, "0"):
            try:
                resource_id = int(raw_id)
            except (TypeError, ValueError):
                continue
        # write 已经隐含 read，重复的 read 行没有意义
        key = (resource_type, resource_id, action)
        if key in seen:
            continue
        seen.add(key)
        await db.execute(
            text(
                "INSERT INTO admin_user_permission "
                "(user_id, resource_type, resource_id, action, created_time) "
                "VALUES (:user_id, :resource_type, :resource_id, :action, NOW())"
            ),
            {
                "user_id": int(user_id),
                "resource_type": resource_type,
                "resource_id": resource_id,
                "action": action,
            },
        )


def has_permission(
    user: dict, permissions: list[dict], resource_type: str, action: str
) -> bool:
    """判断某用户是否可对资源执行动作。super 直接放行。"""
    if normalize_role(user.get("role")) == ROLE_SUPER:
        return True
    needed = str(action or "").strip().lower()
    for item in permissions or []:
        if str(item.get("resource_type") or "") != resource_type:
            continue
        granted = str(item.get("action") or "").lower()
        if granted == "write" or granted == needed:
            return True
    return False
