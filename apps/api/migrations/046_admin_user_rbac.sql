-- 046: 把 admin_user 从「单管理员预留表」扩展为多用户 + RBAC
--
-- 背景：admin_user 表自 023 起就存在，但登录链路一直只比对 .env 里的
-- ADMIN_USERNAME 与单个 admin_password_hash，这张表全项目零引用。
-- 本次迁移补齐子账号所需的字段，并新建一张资源级权限表。
--
-- 幂等：全部 DDL 走 information_schema 判存 + PREPARE，可安全重复执行。
-- 商业版镜像可能已经带过其中若干列，直接 ALTER 会报 Duplicate column name。
--
-- 约定：
--   role = super     超级管理员，唯一，默认 admin，可管理其他账号
--   role = operator  操作员，可操作被授权的资源
--   role = viewer    只读，只能查询
-- 超级管理员不写入 admin_user_permission，权限由 role 直接放行。

SET NAMES utf8mb4;

SET @ddl = IF(
  EXISTS(
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'admin_user' AND column_name = 'role'
  ),
  'SELECT 1',
  'ALTER TABLE `admin_user` ADD COLUMN `role` VARCHAR(20) NOT NULL DEFAULT ''operator'' COMMENT ''super=超级管理员 operator=操作员 viewer=只读'' AFTER `is_super`'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

SET @ddl = IF(
  EXISTS(
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'admin_user' AND column_name = 'account_limit'
  ),
  'SELECT 1',
  'ALTER TABLE `admin_user` ADD COLUMN `account_limit` INT NULL DEFAULT NULL COMMENT ''可绑定闲鱼账号数，NULL 表示不限'' AFTER `role`'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

SET @ddl = IF(
  EXISTS(
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'admin_user' AND column_name = 'created_by'
  ),
  'SELECT 1',
  'ALTER TABLE `admin_user` ADD COLUMN `created_by` BIGINT NULL DEFAULT NULL COMMENT ''创建者 admin_user.id，种子账号为 NULL'' AFTER `account_limit`'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

SET @ddl = IF(
  EXISTS(
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'admin_user' AND column_name = 'last_login_time'
  ),
  'SELECT 1',
  'ALTER TABLE `admin_user` ADD COLUMN `last_login_time` DATETIME NULL DEFAULT NULL AFTER `created_by`'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

SET @ddl = IF(
  EXISTS(
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'admin_user' AND column_name = 'last_login_ip'
  ),
  'SELECT 1',
  'ALTER TABLE `admin_user` ADD COLUMN `last_login_ip` VARCHAR(64) NULL DEFAULT NULL AFTER `last_login_time`'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

SET @ddl = IF(
  EXISTS(
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'admin_user' AND column_name = 'failed_attempts'
  ),
  'SELECT 1',
  'ALTER TABLE `admin_user` ADD COLUMN `failed_attempts` INT NOT NULL DEFAULT 0 COMMENT ''连续失败次数，登录成功清零'' AFTER `last_login_ip`'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

SET @ddl = IF(
  EXISTS(
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'admin_user' AND column_name = 'locked_until'
  ),
  'SELECT 1',
  'ALTER TABLE `admin_user` ADD COLUMN `locked_until` DATETIME NULL DEFAULT NULL COMMENT ''锁定截止时间，NULL 表示未锁定'' AFTER `failed_attempts`'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

SET @ddl = IF(
  EXISTS(
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'admin_user' AND column_name = 'totp_secret'
  ),
  'SELECT 1',
  'ALTER TABLE `admin_user` ADD COLUMN `totp_secret` VARCHAR(64) NULL DEFAULT NULL COMMENT ''TOTP 密钥，空表示未绑定'' AFTER `locked_until`'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

SET @ddl = IF(
  EXISTS(
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'admin_user' AND column_name = 'totp_enabled'
  ),
  'SELECT 1',
  'ALTER TABLE `admin_user` ADD COLUMN `totp_enabled` TINYINT NOT NULL DEFAULT 0 COMMENT ''1=已启用二次验证'' AFTER `totp_secret`'
);
PREPARE migration_stmt FROM @ddl;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;

-- 已有的超管标记同步到新角色字段。is_super=1 的行强制为 super，
-- 逆向不动：role 手工改成 super 但 is_super=0 的行保持原样（service 层按 role 判定）。
UPDATE `admin_user` SET `role` = 'super' WHERE `is_super` = 1 AND `role` <> 'super';

CREATE TABLE IF NOT EXISTS `admin_user_permission` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `user_id` BIGINT NOT NULL COMMENT 'admin_user.id',
  `resource_type` VARCHAR(40) NOT NULL
    COMMENT 'goods=商品 account=闲鱼账号 delivery=发货规则 reply=自动回复',
  `resource_id` BIGINT NULL DEFAULT NULL
    COMMENT '具体资源 id，NULL 表示该类全量',
  `action` VARCHAR(20) NOT NULL COMMENT 'read=查看 write=增删改',
  `created_time` DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_admin_user_permission`
    (`user_id`, `resource_type`, `resource_id`, `action`),
  KEY `idx_admin_user_permission_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='子账号资源级权限';
