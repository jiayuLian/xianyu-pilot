"""子账号 RBAC 集成测试。

不依赖 fastapi/sqlalchemy，直接用 pymysql 复现 services/admin_user_service.py
与 api/v1/routes/admin_users.py 里实际执行的 SQL，验证表结构与语句能对得上。

运行前提：本地 3307 端口有 MySQL 实例，库 xy_mig_test 已执行完全部迁移。
"""

import datetime
import sys

import pymysql

DB = dict(host="127.0.0.1", port=3307, user="root", password="",
          database="xy_mig_test", charset="utf8mb4",
          cursorclass=pymysql.cursors.DictCursor)

USER_COLUMNS = ("id, username, email, password_hash, is_super, status, role, "
                "account_limit, created_by, last_login_time, last_login_ip, "
                "failed_attempts, locked_until, totp_secret, totp_enabled, "
                "created_time, updated_time")

ok_count = 0
fail_count = 0


def check(label, cond, detail=""):
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print("  PASS  %-46s %s" % (label, detail))
    else:
        fail_count += 1
        print("  FAIL  %-46s %s" % (label, detail))


def main():
    conn = pymysql.connect(**DB)
    cur = conn.cursor()

    def q(sql, args=None):
        cur.execute(sql, args or ())
        return cur.fetchall()

    q("DELETE FROM admin_user_permission")
    q("DELETE FROM admin_user")
    conn.commit()

    # ---- 1. 种子超管（ensure_super_admin 的 SQL） ----
    q("""INSERT INTO admin_user
         (username, email, password_hash, is_super, status, role, account_limit,
          created_by, failed_attempts, totp_enabled, created_time, updated_time)
         VALUES (%s, NULL, %s, 1, 1, 'super', NULL, NULL, 0, 0, NOW(), NOW())""",
      ("admin", "$2b$12$seedhash"))
    admin_id = cur.lastrowid
    conn.commit()
    row = q("SELECT %s FROM admin_user WHERE id=%%s" % USER_COLUMNS, (admin_id,))[0]
    check("种子超管 role=super, is_super=1",
          row["role"] == "super" and row["is_super"] == 1, "id=%s" % admin_id)

    # ---- 2. 创建子账号（create_user 的 SQL） ----
    q("""INSERT INTO admin_user
         (username, email, password_hash, is_super, status, role, account_limit,
          created_by, failed_attempts, totp_enabled, created_time, updated_time)
         VALUES (%s, %s, %s, 0, 1, %s, %s, %s, 0, 0, NOW(), NOW())""",
      ("staff1", "s1@example.com", "$2b$12$staffhash", "operator", 3, admin_id))
    staff_id = cur.lastrowid
    conn.commit()
    row = q("SELECT %s FROM admin_user WHERE id=%%s" % USER_COLUMNS, (staff_id,))[0]
    check("创建子账号 operator + account_limit=3",
          row["role"] == "operator" and row["account_limit"] == 3, "id=%s" % staff_id)

    # ---- 3. 按用户名查（get_by_username） ----
    row = q("SELECT %s FROM admin_user WHERE username=%%s LIMIT 1" % USER_COLUMNS,
            ("staff1",))[0]
    check("get_by_username 命中", row["id"] == staff_id,
          "username=%s role=%s" % (row["username"], row["role"]))

    # ---- 4. 失败计数累加到 5 次触发锁定 ----
    for _ in range(5):
        cur_row = q("SELECT failed_attempts FROM admin_user WHERE id=%s", (staff_id,))[0]
        attempts = int(cur_row["failed_attempts"]) + 1
        if attempts >= 5:
            q("""UPDATE admin_user SET failed_attempts=%s,
                   locked_until=DATE_ADD(NOW(), INTERVAL %s MINUTE),
                   updated_time=NOW() WHERE id=%s""",
              (attempts, 15, staff_id))
        else:
            q("""UPDATE admin_user SET failed_attempts=%s, updated_time=NOW()
                 WHERE id=%s""", (attempts, staff_id))
    conn.commit()
    row = q("SELECT failed_attempts, locked_until FROM admin_user WHERE id=%s",
            (staff_id,))[0]
    check("连续 5 次失败后写入 locked_until",
          row["failed_attempts"] == 5 and row["locked_until"] is not None,
          "locked_until=%s" % row["locked_until"])

    # ---- 5. lock_remaining_seconds 的算法 ----
    remain = int((row["locked_until"] - datetime.datetime.now()).total_seconds())
    check("剩余锁定秒数落在 14 分钟区间", 780 <= remain <= 900,
          "remain=%ss -> %s 分钟" % (remain, remain // 60 + 1))

    # ---- 6. 登录成功清零（mark_login_success 的 SQL） ----
    q("""UPDATE admin_user SET failed_attempts=0, locked_until=NULL,
           last_login_time=NOW(), last_login_ip=%s, updated_time=NOW() WHERE id=%s""",
      ("203.0.113.9", staff_id))
    conn.commit()
    row = q("""SELECT failed_attempts, locked_until, last_login_ip
               FROM admin_user WHERE id=%s""", (staff_id,))[0]
    check("登录成功清零并记录 IP",
          row["failed_attempts"] == 0 and row["locked_until"] is None
          and row["last_login_ip"] == "203.0.113.9")

    # ---- 7. 权限写入 + 去重（replace_permissions 的 SQL） ----
    perms = [("goods", None, "read"), ("account", 1, "write"),
             ("delivery", None, "read"), ("reply", None, "write")]
    for p in perms:
        q("""INSERT INTO admin_user_permission
             (user_id, resource_type, resource_id, action, created_time)
             VALUES (%s, %s, %s, %s, NOW())""", (staff_id,) + p)
    conn.commit()
    got = q("""SELECT resource_type, resource_id, action
               FROM admin_user_permission WHERE user_id=%s ORDER BY id""", (staff_id,))
    check("写入 4 条权限", len(got) == 4, "%s" % [g["resource_type"] for g in got])

    # ---- 8. has_permission 的判定逻辑 ----
    def has_permission(user_role, permissions, resource, action):
        if user_role == "super":
            return True
        for item in permissions:
            if item["resource_type"] != resource:
                continue
            if item["action"] == "write" or item["action"] == action:
                return True
        return False

    check("operator 对 goods 有 read", has_permission("operator", got, "goods", "read"))
    check("operator 对 goods 无 write", not has_permission("operator", got, "goods", "write"))
    check("operator 对 account 有 write", has_permission("operator", got, "account", "write"))
    check("operator 对 kami 无任何权限", not has_permission("operator", got, "kami", "read"))
    check("super 绕过权限表", has_permission("super", [], "kami", "write"))

    # ---- 9. 去重：唯一索引对 NULL 不生效，靠 replace_permissions 服务层收敛 ----
    def dedupe(permissions):
        seen, out = set(), []
        for item in permissions:
            rt = str(item.get("resourceType") or "").strip()
            act = str(item.get("action") or "").strip().lower()
            if not rt or act not in ("read", "write"):
                continue
            raw = item.get("resourceId")
            rid = None if raw in (None, "", 0, "0") else int(raw)
            key = (rt, rid, act)
            if key in seen:
                continue
            seen.add(key)
            out.append((rt, rid, act))
        return out

    dup_input = [
        {"resourceType": "goods", "action": "read"},
        {"resourceType": "goods", "action": "read"},
        {"resourceType": "account", "resourceId": 1, "action": "write"},
        {"resourceType": "account", "resourceId": 1, "action": "write"},
        {"resourceType": "delivery", "action": "bogus"},
    ]
    cleaned = dedupe(dup_input)
    check("replace_permissions 入参去重", len(cleaned) == 2, "%s" % cleaned)
    check("非法 action 被丢弃",
          all(a in ("read", "write") for _, _, a in cleaned))

    # MySQL 唯一索引对 resource_id=NULL 不判冲突，这是已知语义，
    # 所以表上加不了「同一全量权限只能一行」的约束，必须靠服务层保证。
    null_dup_insertable = False
    try:
        q("""INSERT INTO admin_user_permission
             (user_id, resource_type, resource_id, action, created_time)
             VALUES (%s, 'goods', NULL, 'read', NOW())""", (staff_id,))
        conn.commit()
        null_dup_insertable = True
    except Exception:
        conn.rollback()
    check("确认唯一索引对 NULL 不生效（故服务层去重）", null_dup_insertable)
    # 清掉这条测试数据，避免影响后续计数
    q("DELETE FROM admin_user_permission WHERE user_id=%s AND resource_type='goods'",
      (staff_id,))
    conn.commit()

    # ---- 10. 用户名唯一约束 ----
    dup_user_blocked = False
    try:
        q("""INSERT INTO admin_user
             (username, password_hash, role, status, created_time, updated_time)
             VALUES ('admin', 'h', 'operator', 1, NOW(), NOW())""")
        conn.commit()
    except Exception:
        dup_user_blocked = True
        conn.rollback()
    check("重复用户名被拦截", dup_user_blocked)

    # ---- 11. 级联删除权限（delete_user 的 SQL） ----
    q("DELETE FROM admin_user_permission WHERE user_id=%s", (staff_id,))
    q("DELETE FROM admin_user WHERE id=%s", (staff_id,))
    conn.commit()
    left_users = q("SELECT COUNT(*) c FROM admin_user")[0]["c"]
    left_perms = q("SELECT COUNT(*) c FROM admin_user_permission")[0]["c"]
    check("删除子账号后权限清空", left_users == 1 and left_perms == 0,
          "users=%s perms=%s" % (left_users, left_perms))

    # ---- 12. 停用账号的 status 语义（validate_admin_credentials 用 int(status) 判） ----
    q("UPDATE admin_user SET status=0 WHERE id=%s", (admin_id,))
    row = q("SELECT status FROM admin_user WHERE id=%s", (admin_id,))[0]
    check("status=0 判定为停用", not int(row["status"]))
    q("UPDATE admin_user SET status=1 WHERE id=%s", (admin_id,))

    # ---- 13. role 归一化（normalize_role 的 Python 逻辑） ----
    def normalize_role(value):
        raw = str(value or "").strip().casefold()
        if raw in ("super", "admin", "root"):
            return "super"
        if raw in ("viewer", "readonly", "read"):
            return "viewer"
        return "operator"

    check("历史 token 的 admin 映射为 super", normalize_role("admin") == "super")
    check("readonly 映射为 viewer", normalize_role("readonly") == "viewer")
    check("未知值落到 operator", normalize_role("whatever") == "operator")

    conn.close()

    print("\n通过 %d 项，失败 %d 项" % (ok_count, fail_count))
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
