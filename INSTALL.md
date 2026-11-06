# 部署安装手册

从零到能用的完整步骤。两种部署方式，按机器条件二选一。

---

## 0. 怎么选

| | Docker 部署 | 裸机部署 |
|---|---|---|
| 适合 | 有 Docker 或愿意装 Docker 的服务器 | 已有 MySQL/Redis 或不便用 Docker |
| 依赖隔离 | 好，全部在容器内 | 差，直接装在系统上 |
| 首次耗时 | 构建镜像 5~10 分钟 | 装依赖 + 装依赖包 15~30 分钟 |
| 磁盘占用 | 约 6~8 GB | 约 4~5 GB |
| 升级 | `git pull` + 重新构建 | `git pull` + 重启进程 |
| 推荐 | ✅ | 仅在前者不可行时用 |

两种方式**端口不同**，可以装在同一台机器上互不干扰：

- Docker：Web `8080`
- 裸机：Web `15176`，API `15177`，Crawler `15178`

两者默认端口被占用时都会**自动往后找可用端口并写回配置文件**，不需要手工处理。

---

## 1. 通用前置条件

| 项 | 最低 | 推荐 |
|---|---|---|
| CPU | 2 核 | 2 核 |
| 内存 | 3 GB | 4 GB |
| 磁盘可用 | 10 GB | 20 GB |
| 系统 | Ubuntu 20.04 / Debian 11 / CentOS 7+ | Ubuntu 22.04 |
| 架构 | x86_64 / arm64 | x86_64 |

检查：

```bash
free -h          # 内存
df -h .          # 磁盘
uname -m         # 架构
cat /etc/os-release | head -3
```

内存低于 3 GB 时 Docker 构建阶段可能被 OOM 杀掉，建议先加 swap：

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 2. 方式一：Docker 部署

### 2.1 安装 Docker（已装可跳过）

确认是否已装：

```bash
docker --version && docker compose version
```

两条都有输出则跳到 2.2。只装了 `docker` 没装 `docker compose` 插件（`docker-compose` 是旧的 Python 版，本项目的 compose 文件用了 `name:` 字段，需要 v2）：

```bash
# Ubuntu / Debian
sudo apt update && sudo apt install -y docker-compose-plugin

# CentOS / RHEL / Fedora
sudo yum install -y docker-compose-plugin
```

完全没有 Docker，用官方脚本（国内网络慢可换阿里云镜像）：

```bash
# 官方源
curl -fsSL https://get.docker.com | sudo sh

# 国内加速（先换镜像源再装）
curl -fsSL https://get.docker.com | sudo sh -s -- --mirror Aliyun
```

装完启动并设开机自启：

```bash
sudo systemctl enable --now docker
```

非 root 用户加进 docker 组，否则每条 docker 命令都要 sudo：

```bash
sudo usermod -aG docker $USER
newgrp docker          # 或重新登录
```

验证：

```bash
docker run --rm hello-world
docker compose version
```

> 国内拉镜像慢，配一个加速器：
> ```bash
> sudo mkdir -p /etc/docker
> sudo tee /etc/docker/daemon.json <<'EOF'
> { "registry-mirrors": ["https://docker.m.daocloud.io", "https://dockerproxy.com"] }
> EOF
> sudo systemctl restart docker
> ```

### 2.2 获取代码

```bash
cd /www/wwwroot            # 或任意你有写权限的目录
git clone https://github.com/jiayuLian/xianyu-pilot.git
cd xianyu-pilot
```

没有 git：

```bash
# Ubuntu / Debian
sudo apt install -y git
# CentOS
sudo yum install -y git
```

### 2.3 一键部署

```bash
sh ./start.sh
```

这一条命令做了全部事情：

1. 检查磁盘空间
2. 检测 Docker，没装就自动装
3. 首次运行时生成 `secrets/`（MySQL 三套密码、JWT、Cookie 加密密钥、内部 token，全部 32~64 字节随机）和 `.env`
4. 检查 `8080` 是否被占用，占用就自动换端口并写回 `.env`
5. 本地源码构建镜像（首次 5~10 分钟）
6. 用构建好的镜像生成 admin 密码哈希写入 `secrets/admin-password-hash`
7. `docker compose up -d` 启动 7 个容器
8. 分 5 个阶段等待健康检查（MySQL → Redis → migrate → api → web）
9. 打印访问地址和默认账号

首次构建输出大致如下，看到 `全部服务就绪` 即成功：

```
• 磁盘可用空间：45GB
• 首次启动，运行初始化向导...
✓ 初始化完成
• 本地源码构建镜像（首次约 5-10 分钟）...
✓ 端口检查通过
• 启动服务...
• 等待服务就绪（分阶段，最长 5 分钟）...
  [1/5] MySQL... ✓
  [2/5] Redis... ✓
  [3/5] 数据库迁移... ✓
  [4/5] API... ✓
  [5/5] Web... ✓

访问地址：
  本机：    http://localhost:8080
默认账号：admin
默认密码：admin123（首次启动时由脚本生成，请尽快修改）
```

### 2.4 端口冲突

`start.sh` 会自动处理，无需手工干预。

**8080 被占用**时自动从 8081 往后找，找到后写入 `.env` 的 `WEB_PORT`，下次启动直接复用：

```
! 端口 8080 已被占用，自动查找可用端口...
• 已自动切换到可用端口：8081
✓ 已将 .env 中的 WEB_PORT 更新为 8081
```

**8081~8090 都被占用**时脚本报错退出，此时手工指定：

```bash
sed -i 's/^WEB_PORT=.*/WEB_PORT=9200/' .env
sh ./start.sh
```

自己先查一下谁占着端口：

```bash
sudo ss -tlnp | grep :8080
# 或
sudo lsof -i :8080
```

改端口后，之前访问的地址也要跟着变（`http://IP:9200`）。

### 2.5 自定义管理员密码

默认 `admin` / `admin123`。想在首次启动就换掉，跑脚本时传环境变量：

```bash
ADMIN_PASSWORD='你的强密码' sh ./start.sh
```

已经部署完想改，用后台的「个人中心 → 修改密码」，或直接改库（不推荐）。

`admin123` 这种弱密码只适合内网首次登录，暴露到公网前必须换掉。

### 2.6 检查容器状态

```bash
docker compose ps
```

7 个服务应该都是 `Up` 或 `Up (healthy)`：

```
NAME                          STATUS
xianyu-assistant-api-1        Up (healthy)
xianyu-assistant-crawler-1    Up (healthy)
xianyu-assistant-migrate-1    Exited (0)        ← 迁移跑完退出是正常的
xianyu-assistant-mysql-1      Up (healthy)
xianyu-assistant-redis-1      Up (healthy)
xianyu-assistant-web-1        Up (healthy)
xianyu-assistant-worker-1     Up (healthy)
```

`migrate` 显示 `Exited (0)` 表示迁移成功跑完，不是故障。

---

## 3. 方式二：裸机部署（无 Docker）

全部装在系统上，直接跑 Python 和 Node。

### 3.1 安装系统依赖

**Ubuntu / Debian**

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl openssl \
                    build-essential libssl-dev mysql-client redis-tools
```

Python 需要 3.10+，Ubuntu 20.04 自带 3.8，需要装新版：

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev
```

**CentOS / RHEL / Rocky**

```bash
sudo yum install -y python3 python3-devel git curl openssl gcc gcc-c++ make
sudo yum install -y mysql
sudo yum install -y redis
```

**Node.js 22**（用 nvm 装最省事，不会污染系统）

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
source ~/.bashrc
nvm install 22
nvm use 22
node --version     # 应为 v22.x
```

或用 NodeSource：

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
```

> 项目 `package.json` 锁定了 `node: 22.23.1`，版本不一致时 `npm install` 会因 engines 检查失败。用别的版本就加参数：
> ```bash
> npm install --engine-strict=false
> ```

### 3.2 安装 MySQL 8

**Ubuntu / Debian**

```bash
sudo apt install -y mysql-server
sudo systemctl enable --now mysql
```

**CentOS 8+ / Rocky**

```bash
sudo dnf install -y https://dev.mysql.com/get/mysql80-community-release-el8-9.noarch.rpm
sudo dnf install -y mysql-community-server
sudo systemctl enable --now mysqld
# 取初始密码
sudo grep 'temporary password' /var/log/mysqld.log
```

初始化安全配置（设 root 密码、删匿名用户）：

```bash
sudo mysql_secure_installation
```

验证：

```bash
mysql -uroot -p -e "SELECT VERSION();"
# 应为 8.0.x
```

字符集确认（必须是 utf8mb4，否则中文事件名会存成问号）：

```bash
mysql -uroot -p -e "SHOW VARIABLES LIKE 'character_set_server';"
```

如果不是 `utf8mb4`，编辑 `/etc/mysql/my.cnf` 或 `/etc/my.cnf`：

```ini
[mysqld]
character-set-server = utf8mb4
collation-server = utf8mb4_unicode_ci
default-time-zone = '+08:00'
```

然后 `sudo systemctl restart mysql`。

> 数据库和用户不需要手工建，初始化脚本会建。只要你提供 root 密码即可。

### 3.3 安装 Redis 7

**Ubuntu / Debian**

```bash
sudo apt install -y redis-server
sudo systemctl enable --now redis-server
```

**CentOS / Rocky**

```bash
sudo yum install -y redis
sudo systemctl enable --now redis
```

验证：

```bash
redis-cli ping      # 返回 PONG
```

Redis 有密码时（默认没有），需要把密码写进 `.env` 的 `REDIS_PASSWORD`，否则 API 连不上。

### 3.4 初始化

```bash
cd /www/wwwroot/xianyu-pilot
sh ./scripts/setup-local.sh
```

脚本按顺序做：

1. 检查 Python 3.10+、Node.js、npm
2. 从 `.env.development.example` 生成 `.env`，自动填随机 `JWT_SECRET`、`COOKIE_CRYPTO_SECRET`、`INTERNAL_API_TOKEN`、`MYSQL_PASSWORD`
3. 建 `.venv` 虚拟环境，装 `apps/api/requirements.txt`
4. 生成 admin 的 bcrypt 密码哈希写入 `.env` 的 `ADMIN_PASSWORD_HASH`
5. 用 MySQL root 建库 `xianyu_opensource` 和用户 `xianyu`，授权
6. 装 Crawler 和 Web 的 npm 依赖
7. 下载 Playwright Chromium（约 150 MB）

MySQL root 有密码时，脚本会提示输入，也可以提前传：

```bash
MYSQL_ROOT_PASSWORD='你的root密码' sh ./scripts/setup-local.sh
```

想换 admin 密码：

```bash
ADMIN_PASSWORD='你的强密码' sh ./scripts/setup-local.sh
```

国内网络加速：

```bash
PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple" \
PLAYWRIGHT_DOWNLOAD_HOST="https://npmmirror.com/mirrors/playwright" \
MYSQL_ROOT_PASSWORD='你的root密码' \
sh ./scripts/setup-local.sh
```

跑完看到：

```
✓ 本地初始化完成

默认管理员账号：
  用户名：admin
  密码：admin123（登录后请尽快修改）

下一步：
  启动服务：sh ./start-local.sh
```

### 3.5 启动

```bash
sh ./start-local.sh
```

它做了：

1. 检查 `.env`、`.venv`、`node_modules` 是否齐全，缺就自动跑一遍初始化
2. 检查 Redis 连通性（不通只警告，不中断）
3. 分配端口，冲突就自动换
4. 执行数据库迁移 `python -m app.migrations upgrade`
5. 构建 Crawler（`npm run build`）
6. 后台启动 4 个进程：API、Worker、Crawler、Web
7. 逐个等健康检查通过

输出：

```
✓ 端口检查通过（Web=15176, API=15177, Crawler=15178）
✓ 数据库迁移完成
✓ Crawler 构建完成
• 启动 API...
• 启动 Worker...
• 启动 Crawler...
• 启动 Web...
  Crawler ✓
  API ✓
  Web ✓

访问地址：
  本机：   http://localhost:15176
  局域网： http://192.168.1.10:15176
```

### 3.6 端口冲突

和 Docker 版一样自动处理，区别是**三个端口都要检查**，且互不重复：

```
! Web 端口 15176 被占用，改用 15179
! API 端口 15177 被占用，改用 15180
✓ 端口检查通过（Web=15179, API=15180, Crawler=15178）
```

换过的端口会写回 `.env` 的 `XYA_WEB_PORT` / `SERVER_PORT` / `CRAWLER_PORT`，下次启动直接复用，不会来回漂。

脚本从原端口往后找 30 个，全被占用才报错。这时手工改 `.env`：

```bash
sed -i 's/^SERVER_PORT=.*/SERVER_PORT=16177/' .env
sed -i 's/^CRAWLER_PORT=.*/CRAWLER_PORT=16178/' .env
printf 'XYA_WEB_PORT=16176\n' >> .env
sh ./start-local.sh
```

### 3.7 停止 / 状态 / 日志

```bash
sh ./stop-local.sh                  # 停止全部
sh ./status-local.sh                # 查看状态
```

日志在 `output/local-dev/`：

```bash
tail -f output/local-dev/api.log
tail -f output/local-dev/web.log
tail -f output/local-dev/crawler.log
tail -f output/local-dev/worker.log
```

---

## 4. 网页端初始化（两种方式通用）

打开浏览器访问上一步打印的地址（Docker 默认 `http://服务器IP:8080`，裸机默认 `http://服务器IP:15176`）。

### 4.1 登录

| | 值 |
|---|---|
| 用户名 | `admin` |
| 密码 | `admin123`（或你初始化时用 `ADMIN_PASSWORD` 指定的密码） |

如果页面提示「用户名或密码错误」，说明初始化时生成的哈希有问题。重新生成：

```bash
# Docker：改宿主机上的 secrets 文件后重启（容器 rootfs 是只读的，不能直接写 /run/secrets）
docker compose run --rm --no-deps api python -c \
  "import bcrypt;print(bcrypt.hashpw(b'admin123',bcrypt.gensalt(rounds=12)).decode())" \
  | tr -d '\r\n' > secrets/admin-password-hash
chmod 600 secrets/admin-password-hash
docker compose up -d

# 裸机
.venv/bin/python -c "import bcrypt;print(bcrypt.hashpw(b'admin123',bcrypt.gensalt(rounds=12)).decode())"
# 把输出写进 .env 的 ADMIN_PASSWORD_HASH=...，然后：
sh ./stop-local.sh && sh ./start-local.sh
```

> 数据库里 `admin_user` 表在首次启动时会把 `.env` 的 admin 种进去。如果表里已经有账号，
> 种子逻辑会跳过，改 `.env` 的密码哈希不会生效——此时改密码要用后台的「修改密码」功能，
> 或由超管在「子账号」页重置。要让新哈希生效，先清空表里的 admin 记录再重启：
>
> ```bash
> # Docker
> docker compose exec mysql mysql -uroot -p"$(cat secrets/mysql-root-password)" \
>   xianyu_opensource -e "DELETE FROM admin_user WHERE username='admin';"
> # 裸机
> mysql -uxianyu -p"$(grep '^MYSQL_PASSWORD=' .env | cut -d= -f2)" xianyu_opensource \
>   -e "DELETE FROM admin_user WHERE username='admin';"
> ```

### 4.2 立刻改密码

右上角头像 → 个人中心 → 修改密码。要求：至少 6 位、不能包含用户名、不能是常见弱密码。

### 4.3 绑定闲鱼账号

左侧「账号管理」→ 添加账号 → 用手机闲鱼 App 扫码登录。

成功后账号状态会显示「保活成功」。这一步的 cookie 保存失败，后面所有自动发货都不会工作。

### 4.4 配置自动发货

左侧「商品管理」→ 同步商品 → 找到要自动发货的商品 → 配置发货规则。

规则至少要有：

- 触发条件（付款后自动发货）
- 发货内容（卡密 / 文本 / 图片 / 网盘链接）

配置完的规则才能匹配订单。**没有规则的商品，付款后不会自动发货**。

### 4.5 配置通知（可选）

左侧「系统 → 通知设置」。

可用的 7 个事件：

| 事件 | 建议 |
|---|---|
| Cookie 到期 | 开 |
| 账号掉线 | 开 |
| 人机验证 | 开 |
| 自动发货失败 | 开 |
| 新订单提醒 | 关（闲鱼 App 自带推送） |
| 自动发货成功 | 关 |
| 人机验证成功 | 关 |

渠道支持 PushPlus / 飞书 / 钉钉 / 企业微信 / 邮件。PushPlus 的 token 填在该渠道的 `receiver` 字段。

页面上方的「启用浏览器通知」按钮会申请浏览器通知权限，开启后异常事件会弹到桌面右下角（需要页面保持打开）。

### 4.6 创建子账号

左侧「系统 → 子账号」（仅超级管理员可见）。

- **角色**：`操作员` 按权限表操作；`只读` 只能查看，任何写操作返回 403
- **账号配额**：限制该子账号能绑多少个闲鱼号，留空不限
- **权限矩阵**：商品 / 闲鱼账号 / 发货规则 / 自动回复 × 查看 / 修改

权限在 60 秒内生效（Redis 缓存），保存权限时会主动失效，通常立即生效。

子账号用同一入口登录，登录后侧栏不显示「子账号」菜单，写操作按权限放行。

---

## 5. 验证清单

部署完按顺序跑一遍，全过就算装好了。

**① 容器/进程状态**（Docker）

```bash
docker compose ps
```

7 个服务，除 `migrate` 外都是 `Up (healthy)`。

**② 健康检查**

```bash
# Docker：web 容器的 nginx 提供 /readyz
curl -fsS http://127.0.0.1:8080/readyz

# 裸机：API 提供 /health/ready（注意是 API 端口 15177，不是 Web 端口）
curl -fsS http://127.0.0.1:15177/health/ready
```

Docker 的 `/readyz` 返回 `ok`，裸机的 `/health/ready` 返回 JSON 即正常。

**③ 登录接口**

```bash
curl -sS -X POST http://127.0.0.1:8080/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}'
```

返回里的 `role` 应为 `super`，并带 `token` 字段。

**④ 表结构**

```bash
# Docker
docker compose exec mysql mysql -uroot -p"$(cat secrets/mysql-root-password)" \
  xianyu_opensource -e "SHOW TABLES LIKE 'admin%'; DESC admin_user;"

# 裸机
mysql -uxianyu -p"$(grep '^MYSQL_PASSWORD=' .env | cut -d= -f2)" xianyu_opensource \
  -e "SHOW TABLES LIKE 'admin%'; DESC admin_user;"
```

应该看到 `admin_user` 和 `admin_user_permission` 两张表，`admin_user` 里有 `role`、`failed_attempts`、`locked_until`、`totp_secret` 等列。

**⑤ 迁移版本**

```bash
# Docker
docker compose exec mysql mysql -uroot -p"$(cat secrets/mysql-root-password)" \
  xianyu_opensource -e "SELECT version FROM schema_migration ORDER BY version DESC LIMIT 3;"
```

最新的应是 `046`。

**⑥ 日志无报错**

```bash
# Docker
docker compose logs --tail 100 api | grep -iE 'error|traceback'

# 裸机
grep -iE 'error|traceback' output/local-dev/api.log | tail -20
```

空的就对了。

---

## 6. 暴露到公网

默认 `WEB_BIND_ADDRESS=0.0.0.0`，端口直接对外。**这样上线是不安全的**，正确做法是绑回本地 + 反代。

改 `.env`：

```ini
WEB_BIND_ADDRESS=127.0.0.1
WEB_PORT=8080
TRUSTED_HOSTS=xy.example.com,localhost,127.0.0.1,api
```

Nginx 反代（宝塔：网站 → 设置 → 反向代理 → 目标 `http://127.0.0.1:8080`）：

```nginx
server {
    listen 443 ssl http2;
    server_name xy.example.com;

    ssl_certificate     /etc/letsencrypt/live/xy.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/xy.example.com/privkey.pem;

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 需要关缓冲，否则实时状态不刷新
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
```

`TRUSTED_PROXY_IPS` 要包含 Nginx 的出口 IP，否则拿到的客户端 IP 是代理地址，登录失败计数会把所有人算成同一个：

```ini
TRUSTED_PROXY_IPS=172.30.240.20/32
```

改完重启：

```bash
docker compose up -d       # Docker
sh ./start-local.sh        # 裸机
```

---

## 7. 备份与恢复

**Docker**

```bash
mkdir -p /root/backup
docker compose exec -T mysql mysqldump -uroot -p"$(cat secrets/mysql-root-password)" \
  --default-character-set=utf8mb4 --single-transaction xianyu_opensource \
  | gzip > /root/backup/db_$(date +%Y%m%d_%H%M).sql.gz

# 连同 secrets 一起备（丢了 secrets 旧数据全解不开）
tar czf /root/backup/secrets_$(date +%Y%m%d).tar.gz secrets .env
```

**裸机**

```bash
mysqldump -uxianyu -p"$(grep '^MYSQL_PASSWORD=' .env | cut -d= -f2)" \
  --default-character-set=utf8mb4 --single-transaction xianyu_opensource \
  | gzip > /root/backup/db_$(date +%Y%m%d_%H%M).sql.gz
tar czf /root/backup/env_$(date +%Y%m%d).tar.gz .env
```

恢复：

```bash
gunzip -c /root/backup/db_xxx.sql.gz | \
  docker compose exec -T mysql mysql -uroot -p"$(cat secrets/mysql-root-password)" xianyu_opensource
```

`secrets/` 目录里的密钥是加密 cookie 和密码的盐。**换机器时 secrets 必须一起搬**，只搬数据库会导致已保存的闲鱼登录态全部失效。

---

## 8. 升级

**Docker**

```bash
cd /www/wwwroot/xianyu-pilot
git pull
sh ./start.sh                  # 自动重新构建 + 迁移 + 重启
```

**裸机**

```bash
cd /www/wwwroot/xianyu-pilot
git pull
.venv/bin/pip install -r apps/api/requirements.txt
(cd apps/crawler && npm install && npm run build)
(cd apps/web && npm install && npm run build)
sh ./stop-local.sh && sh ./start-local.sh
```

迁移在启动时自动执行，`schema_migration` 表记录已执行的版本，重复执行会被跳过。

---

## 9. 常见故障

**`docker compose pull` 拉到的是旧代码**

`docker-compose.yml` 的 `image:` 默认指向官方镜像。用 `sh ./start.sh`（默认走本地构建）而不是 `--pull`。加了 `--pull` 会覆盖本地构建结果。

**API 启动即退出，日志报 `SchemaDriftError`**

数据库里有比当前代码更新的迁移版本。说明你用旧代码连了新库，或回滚了代码没回滚库。重新 `git pull` 到最新代码再启动。

**日志里中文全是 `???`**

MySQL 字符集不是 utf8mb4。查 `SHOW VARIABLES LIKE 'character_set_server';`，不是就改 `my.cnf` 重启。

**时间比实际早 8 小时**

容器时区是 UTC。`docker-compose.yml` 已给所有服务注入 `TZ: ${TZ:-Asia/Shanghai}`，如果你的 `.env` 里手动覆盖了 `TZ`，去掉它。

**登录后立刻 401**

Redis 没连上，token 吊销列表读不到。检查 `docker compose ps redis`，或裸机 `redis-cli ping`。

**子账号创建后登录提示「用户名或密码错误」**

确认子账号的 `status` 是 1（启用），且密码满足强度要求（≥6 位、不含用户名）。

**子账号点保存提示 403**

这是权限守卫在起作用。「操作员」需要在权限矩阵里勾上对应资源的「修改」；「只读」角色一律不能写。

**`npm install` 报 engines 错误**

Node 版本不是 22.23.1。加 `--engine-strict=false`，或 `nvm install 22`。

**Playwright Chromium 下载失败**

```bash
cd apps/crawler
PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright npm exec playwright install chromium
```

**端口改了但页面打不开**

改的是 `.env` 的端口，访问地址要跟着换。`docker compose port web` 或 `sh ./status-local.sh` 能看到实际端口。

---

## 10. 卸载

**Docker**

```bash
cd /www/wwwroot/xianyu-pilot
docker compose down              # 停止并删容器，保留数据卷
docker compose down -v           # 连数据卷一起删（数据库、上传文件全没）
docker rmi $(docker images | grep xianyu-assistant | awk '{print $3}')
```

**裸机**

```bash
cd /www/wwwroot/xianyu-pilot
sh ./stop-local.sh
rm -rf .venv node_modules apps/*/node_modules output
# MySQL 里的库需手工删
mysql -uroot -p -e "DROP DATABASE xianyu_opensource; DROP USER 'xianyu'@'localhost';"
```
