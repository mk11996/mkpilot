# openpilot 本地服务器项目方案

## 1. 项目目标

在威联通 NAS 的 Docker 环境中，建立一套兼容 openpilot 原生上传模式的个人服务器，实现：

- 简化设备注册；
- 使用设备公钥和短期 JWT 认证；
- 通过 `upload_url + HTTP PUT` 上传原生日志、视频和自定义 CSV；
- 通过 Athena WebSocket 提供设备在线状态和远程管理；
- 为手机 App 和网页端提供统一 HTTPS API；
- 不依赖 comma 官方注册、Connect、Athena 和日志存储服务。

第一阶段只实现文件上传和安全设备管理，不实现远程方向盘、油门、刹车或强制启用驾驶控制。

## 2. 最终架构

```text
openpilot 设备
  ├─ 设备公钥 / JWT
  ├─ REST：注册、申请上传地址
  ├─ HTTP PUT：上传文件
  └─ WSS Athena：状态、远程管理、上传队列
          │
          ▼
      Caddy（HTTPS/WSS）
          │
          ▼
      FastAPI Server
       ├─ Enrollment
       ├─ JWT Auth
       ├─ Upload URL
       ├─ File Metadata
       ├─ Athena WebSocket
       └─ Mobile/Web API
          │             │
          ▼             ▼
      PostgreSQL       MinIO/NAS 文件目录
```

## 3. Docker 服务

第一版部署以下容器：

| 服务 | 作用 |
|---|---|
| `caddy` | HTTPS、WSS、反向代理 |
| `openpilot-server` | 设备注册、认证、上传、Athena、App API |
| `postgres` | 设备、文件、行程、审计记录 |
| `minio` 或 NAS 文件目录 | 保存原始日志、视频和 CSV |

初期不需要 Redis。只有部署多个 API 实例或设备数量增加后，再加入 Redis 保存 Athena 在线状态。

## 4. 设备注册设计

### 4.1 一次性注册码

服务器后台生成短期注册码，例如：

```text
MPPT-7K3D-91QF
```

注册码默认 10 分钟有效，只能绑定一台设备。

### 4.2 注册请求

```http
POST /api/v1/enroll
Content-Type: application/json
```

```json
{
  "enroll_code": "MPPT-7K3D-91QF",
  "public_key": "-----BEGIN PUBLIC KEY-----...",
  "device_name": "Mazda openpilot"
}
```

### 4.3 注册响应

```json
{
  "device_id": "a1b2c3d4e5f60708",
  "device_name": "Mazda openpilot",
  "server_time": 1780000000
}
```

服务器保存设备公钥。以后设备使用 `/data/persist/comma/id_rsa` 对 JWT 签名，服务器用对应公钥验证。

不再使用 IMEI、SIM 卡、comma 账号和官方 `pilotauth` 注册流程。

## 5. JWT 认证

JWT 使用 RS256，载荷保持接近 openpilot 原生格式：

```json
{
  "identity": "a1b2c3d4e5f60708",
  "nbf": 1780000000,
  "iat": 1780000000,
  "exp": 1780003600
}
```

REST 请求使用：

```http
Authorization: JWT <token>
```

Athena WebSocket 使用：

```http
Cookie: jwt=<token>
```

服务器必须检查：

- 签名是否正确；
- `identity` 是否对应设备；
- `nbf`、`iat`、`exp` 是否有效；
- 设备是否被撤销；
- 请求 IP 和设备行为是否异常。

## 6. 原生上传协议兼容

### 6.1 申请上传地址

```http
GET /v1.4/{device_id}/upload_url/?path={relative_path}
Authorization: JWT <token>
```

服务器返回：

```json
{
  "url": "https://server.example.com/storage/put/<temporary-token>",
  "headers": {
    "Content-Type": "application/octet-stream"
  }
}
```

### 6.2 上传文件

设备随后执行：

```http
PUT /storage/put/<temporary-token>
Content-Length: <size>
Content-Type: application/octet-stream
```

服务器返回 `200` 或 `201` 后，openpilot 原生上传器会给本地文件写入已上传标记。

### 6.3 上传地址安全要求

临时上传令牌必须：

- 绑定设备 ID；
- 绑定目标路径；
- 默认 15 分钟过期；
- 最多使用一次；
- 限制文件大小；
- 禁止绝对路径和 `..`；
- 不允许覆盖其他设备文件；
- 上传完成后计算 SHA-256；
- 写入数据库元数据。

## 7. 文件分类和目录

建议服务器目录：

```text
storage/
└── devices/
    └── <device_id>/
        ├── routes/
        │   └── <route_name>/
        │       ├── rlog.bz2
        │       ├── qlog.bz2
        │       ├── qcamera.ts
        │       ├── fcamera.hevc
        │       └── dcamera.hevc
        ├── vehicle-data/
        │   └── 2026-08-04/
        │       └── vehicle_data.csv
        ├── boot/
        └── crash/
```

openpilot 原生日志来自：

```text
/data/media/0/realdata/
```

当前自定义 CSV 来自：

```text
/data/logs/
```

需要修改 openpilot 上传器，使其同时扫描这两个目录，或者将 CSV 加入统一上传队列。

## 8. Athena WebSocket

### 8.1 连接地址

```text
wss://server.example.com/ws/v2/{device_id}
```

### 8.2 初期实现的方法

```text
getVersion
getNetworkType
listUploadQueue
listDataDirectory
takeSnapshot
reboot
uploadFileToUrl
uploadFilesToUrls
cancelUpload
setBandwithLimit
```

设备主动转发的消息：

```text
forwardLogs
storeStats
```

### 8.3 JSON-RPC 示例

服务器请求：

```json
{
  "jsonrpc": "2.0",
  "method": "getVersion",
  "id": 1
}
```

设备响应：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "version": "custom-version",
    "commit": "abcdef0"
  }
}
```

服务端要维护：

- 设备在线表；
- 最近连接时间；
- 最近心跳时间；
- 请求 ID；
- 请求超时；
- 设备断线重连；
- 命令审计日志。

## 9. App API

手机 App 不直接连接 Athena，也不直接访问 NAS 文件目录，只访问服务器 API。

```text
POST /api/v1/app/login
GET  /api/v1/devices
GET  /api/v1/devices/{id}
GET  /api/v1/devices/{id}/status
GET  /api/v1/devices/{id}/routes
GET  /api/v1/devices/{id}/files
GET  /api/v1/files/{id}/download
GET  /api/v1/devices/{id}/snapshot
POST /api/v1/devices/{id}/commands/reboot
POST /api/v1/devices/{id}/commands/upload-queue
```

所有 App 命令都必须写入审计表。

## 10. 数据库表

### devices

```text
id
device_id
name
public_key
firmware_version
last_seen_at
last_ip
revoked
created_at
```

### files

```text
id
device_id
relative_path
file_type
route_name
size_bytes
sha256
storage_path
uploaded_at
```

### routes

```text
id
device_id
route_name
started_at
ended_at
segment_count
distance_m
duration_s
```

### commands

```text
id
device_id
user_id
method
parameters_json
result_json
status
created_at
completed_at
```

### enrollment_codes

```text
code_hash
expires_at
used_at
device_id
```

## 11. openpilot 端修改点

修改以下位置：

```text
common/api/__init__.py
selfdrive/athena/athenad.py
selfdrive/athena/registration.py
selfdrive/manager/process_config.py
selfdrive/loggerd/uploader.py
```

修改内容：

1. `API_HOST` 指向自建服务器；
2. `ATHENA_HOST` 指向自建 WSS；
3. `registration.py` 使用一次性注册码注册；
4. 注册成功后写入 `DongleId`；
5. `uploader.py` 同时扫描原生日志和 `/data/logs/`；
6. 关闭 `log_uploader_smb`；
7. 启用 `uploader`；
8. 启用 `manage_athenad`；
9. 保留原生 xattr 上传成功标记和重试机制。

## 12. 安全方案

- NAS 不暴露 SMB 到公网；
- 只开放 HTTPS/WSS；
- 使用有效 TLS 证书；
- 设备使用独立公钥；
- App 使用独立账号，不使用设备私钥；
- 上传使用短期 URL；
- 数据库不开放公网端口；
- 上传目录禁止执行文件；
- 设备可以撤销；
- 所有远程命令写审计日志；
- NAS 使用独立上传账户；
- 定期备份数据库和原始文件。

另外，现有 `launch_env.sh` 中包含明文 NAS 凭据，应立即更换该凭据并从后续版本中删除。

## 13. 远程控制边界

第一版只开放：

- 查看状态；
- 查看版本；
- 查看上传队列；
- 获取截图；
- 查看文件；
- 远程上传文件；
- 远程重启；
- 设置上传带宽。

禁止通过服务器直接控制方向盘、油门、刹车或绕过 openpilot 安全条件。任何未来的车辆控制功能都必须同时经过设备端的停车、档位、速度和 offroad 状态检查。

## 14. 开发阶段

### 阶段 A：服务器基础

- Docker Compose；
- PostgreSQL；
- HTTPS；
- 设备表；
- 一次性注册码；
- JWT 验证。

验收：设备可以注册，服务器可以识别设备身份。

### 阶段 B：上传兼容

- `upload_url` 接口；
- 临时 PUT URL；
- 文件保存；
- SHA-256；
- 文件索引；
- 上传失败重试。

验收：`qlog.bz2`、`rlog.bz2` 和 `vehicle_data.csv` 均可上传并能防止重复上传。

### 阶段 C：Athena

- WSS；
- JSON-RPC；
- 在线状态；
- `getVersion`；
- `takeSnapshot`；
- `reboot`；
- 审计日志。

验收：服务器可以查看设备状态、获取截图并安全地请求重启。

### 阶段 D：App 和网页

- 登录；
- 设备列表；
- 行程列表；
- 文件下载；
- CSV 预览；
- 设备状态页。

### 阶段 E：增强能力

- 断点续传；
- 视频转码或缩略图；
- 路线地图；
- 统计图表；
- 通知；
- 远程升级；
- 多设备支持。

## 15. 第一版完成标准

- 设备无需 comma 账号即可完成注册；
- 服务器能验证设备 JWT；
- openpilot 原生日志能上传；
- 自定义 CSV 能上传；
- 断网后能够自动重试；
- 同一文件不会重复保存；
- 手机可以查看和下载文件；
- Athena 设备在线状态可靠；
- 所有远程命令有权限检查和审计记录；
- NAS 无需对公网开放 SMB、数据库或管理端口。

