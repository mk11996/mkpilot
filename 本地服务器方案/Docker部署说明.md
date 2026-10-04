# Docker 部署说明

## 推荐目录

```text
local-server/
├── compose.yaml
├── .env
├── server/
├── caddy/
│   └── Caddyfile
├── postgres/
└── storage/
```

## 容器职责

```text
caddy              只暴露 443
openpilot-server   只加入 Docker 内网
postgres           只加入 Docker 内网
minio              只加入 Docker 内网
```

不要将 PostgreSQL、MinIO 管理端口和 Docker 管理接口直接暴露到公网。

## 网络方案

### 仅个人使用

优先使用 Tailscale 或 WireGuard，App 和车辆通过 VPN 访问 NAS。

### 需要公网访问

使用：

```text
域名
TLS 证书
Caddy
HTTPS/WSS
```

只开放：

```text
443/tcp
```

## 数据卷

```text
postgres_data:/var/lib/postgresql/data
minio_data:/data
server_storage:/srv/openpilot
```

## 备份

至少备份：

- PostgreSQL 数据库；
- `/srv/openpilot` 或 MinIO 数据；
- 服务器密钥；
- Caddy 配置；
- 设备公钥记录。

RAID 不能代替备份。

