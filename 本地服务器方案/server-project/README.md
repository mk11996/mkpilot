# openpilot 本地服务器

这是一个面向威联通 NAS Container Station 的第一版服务器工程，目标是兼容 openpilot 的设备上传模式，并为后续手机 App 和远程设备管理提供基础。

## 已实现

- 一次性注册码注册设备；
- 设备公钥登记；
- RS256 设备 JWT 验证；
- 持久化上传令牌；
- 原生 `upload_url + HTTP PUT` 裸二进制上传；
- 原生日志和自定义 CSV 文件索引；
- PostgreSQL 持久化；
- App 管理员登录；
- 设备列表、分类文件列表和下载接口；
- Athena WSS 基础双向 JSON-RPC；
- 安全远程管理命令白名单和审计记录；
- API 请求、上传和 Athena 日志；
- Docker 健康检查；
- Caddy HTTPS/WSS 反向代理。
- FFmpeg 优先无损封装 HEVC/TS 为 MP4 并缓存，失败时才转码为 H.264 MP4。
- API 镜像构建时使用清华 Debian 和 PyPI 镜像，降低国内安装 FFmpeg 与 Python 依赖的失败率。

网页后台地址：

```text
http://NAS_IP:18080/admin/
```

后台支持管理员登录、设备列表、文件查看、下载、删除和视频播放。HEVC/TS 文件优先轻量封装，只有不兼容时才占用 NAS CPU 转码。

文件在存储卷中按设备和用途分类，数据库中的公开路径保持不变，设备端无需修改：

```text
storage/<设备ID>/videos/          视频
storage/<设备ID>/driving-logs/   rlog、qlog、boot 等驾驶日志
storage/<设备ID>/trajectory-csv/ 驾驶轨迹 CSV
storage/<设备ID>/system-errors/  errors/ 下的系统错误日志
```

网页中的驾驶数据统计不包含 `errors/` 系统错误日志；视频/驾驶日志和轨迹 CSV 会按日期分组，点击日期可展开或收起当天文件。

重新部署后，新上传文件会直接进入上述分类目录。此方案不包含历史文件迁移功能；如需保留旧数据，应在重新部署前自行备份原来的 `storage` 目录。

## 目录

```text
server-project/
├── compose.yaml
├── .env.example
├── caddy/Caddyfile
├── server/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/main.py
├── tests/
├── scripts/
└── storage/
```

## 本地或 NAS 启动

```bash
cp .env.example .env
docker compose up -d --build
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

默认只启动 API 和 PostgreSQL，不启动 Caddy，避免与 QTS 的 80/443 端口冲突。需要 HTTPS 时再执行：

```bash
docker compose --profile https up -d --build
```

首次构建需要 NAS 能访问 Docker Hub 和 Python/系统软件源；构建完成后，正常重启不需要重复下载。Container Station 如果使用图形界面导入 Compose 文件，仍需先在项目目录准备好 `.env`，并确认端口没有被 QTS 或其他容器占用。

注意：清华源只覆盖 Debian 软件包和 PyPI 依赖，`postgres:16` 与 `python:3.12-slim` 这两个基础映像仍需要由 Container Station 从 Docker 镜像仓库拉取。本项目使用普通 PostgreSQL 16 映像，不依赖 Alpine 版本；如果自定义仓库无法提供 Alpine 标签，应使用本项目 Compose 中的 `postgres:16`。

检查：

```text
http://服务器地址/health
http://服务器地址/docs
```

## 部署前必须修改

```text
APP_SECRET
DATABASE_URL
LOCAL_ENROLL_CODE
ADMIN_PASSWORD
DIAGNOSTICS_TOKEN
PUBLIC_BASE_URL
SERVER_DOMAIN
```

`PUBLIC_BASE_URL` 必须是车辆设备可以访问的地址，不能使用 `127.0.0.1`。

`POSTGRES_PASSWORD` 必须与 `DATABASE_URL` 中的数据库密码一致。

不要把 `APP_SECRET`、`LOCAL_ENROLL_CODE`、`ADMIN_PASSWORD` 和 `DIAGNOSTICS_TOKEN` 保留为示例值，也不要把 API 端口直接暴露到公网。外网访问应通过 VPN 或已配置证书的 HTTPS 反向代理。

## 端口

API 端口由 `API_PORT` 控制，默认是 `18080`，可以改成其他未占用端口，例如：

```env
API_PORT=28080
PUBLIC_BASE_URL=http://192.168.5.3:18080
```

如果启用 HTTPS profile，Caddy 端口由 `HTTP_PORT` 和 `HTTPS_PORT` 控制。

视频播放说明：服务器保留原始 HEVC 文件，并在第一次点击播放时用 FFmpeg 生成缓存 MP4。HEVC 能否由浏览器直接解码取决于浏览器和设备；如果浏览器不能播放，服务器会回退为 H.264 MP4，这会消耗 NAS CPU 并占用额外缓存空间。网页当前会把播放响应加载到浏览器后再播放，超大视频不适合长期用网页直接下载/播放，后续 App 可改为带鉴权的分段流式接口。

## NAS 首次验收

启动后依次检查：

```text
GET  http://NAS_IP:API_PORT/health       -> {"status":"ok"}
GET  http://NAS_IP:API_PORT/ready        -> {"status":"ready"}
打开 http://NAS_IP:API_PORT/admin/       -> 能登录后台
```

然后用一个测试设备完成注册、获取上传地址、上传一个小 CSV，再在后台确认文件列表和下载。视频建议先测试一个小的 HEVC 文件；第一次播放等待 FFmpeg 生成缓存属于正常现象。

如果 `/health` 正常但 `/ready` 返回 503，优先查看 PostgreSQL 日志；如果设备注册成功但上传失败，检查设备能否访问 `PUBLIC_BASE_URL`，尤其不要把它设置成 `127.0.0.1`、`localhost` 或电脑自己的地址。

## 手机 App 接入

手机 App 后续使用版本化 REST API：

```text
POST /api/v1/app/login
GET  /api/v1/devices
GET  /api/v1/devices/{id}/files
GET  /api/v1/devices/{id}/routes
GET  /api/v1/files/{id}/download
GET  /api/v1/files/{id}/stream
POST /api/v1/devices/{id}/commands/{method}
```

网页后台和手机 App 共用这些 API，不需要直接访问 PostgreSQL 或 NAS 文件夹。

## 查看日志

```bash
docker compose ps
docker compose logs -f api
docker compose logs -f postgres
```

详细排障见 [排障说明.md](排障说明.md)。

## 停止服务

```bash
docker compose down
```

不要随意使用 `docker compose down -v`，否则会删除 PostgreSQL 数据卷。

## 当前安全边界

远程命令只允许设备管理功能，例如查看版本、查看上传队列、截图和重启。没有加入方向盘、油门、刹车或强制启用驾驶控制命令。
