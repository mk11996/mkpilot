# openpilot 公网服务器方案

这是与 NAS 项目完全独立的公网部署副本。它不映射 API 或 PostgreSQL 端口到公网；公网只能访问 Caddy 提供的 HTTPS `443` 与证书验证使用的 `80`。

## 部署前条件

1. 准备一个域名，例如 `op.example.com`。
2. 将该域名的 A 记录（IPv4）或 AAAA 记录（IPv6）指向云服务器公网地址。
3. 在云平台安全组和系统防火墙中，仅放行 `80/TCP`、`443/TCP`、以及仅供自己管理的 `22/TCP`。
4. 不要开放 `18080`、`5432`、`8080`、`8081` 或 `8443`。

## 首次部署

```bash
cd /opt/openpilot-public-server
cp .env.example .env
nano .env
docker compose up -d --build
docker compose ps
docker compose logs -f caddy
```

将 `.env` 中的域名、邮箱及全部密码/密钥替换为真实且唯一的值。Caddy 会在 DNS 已正确生效且 80/443 可从公网访问时，自动申请并续期 HTTPS 证书。

部署成功后只通过下面地址访问：

```text
https://你的域名/admin/
```

## 迁移现有 NAS 数据（可选）

本副本默认是全新数据库和全新存储目录。若要保留 NAS 的设备注册、历史文件及车辆状态，必须同时迁移 PostgreSQL 数据库、`storage/` 文件和现有的 `APP_SECRET`；只复制程序代码不会保留这些数据。

## 更新

上传新文件后执行：

```bash
cd /opt/openpilot-public-server
docker compose up -d --build
docker compose logs --tail 100 api
```

## 设备切换

确认后台与 HTTPS 正常后，再把 openpilot 设备的服务器地址切换为：

```text
https://你的域名
```

设备切换前保持 NAS 服务运行，确认新服务器能注册、Athena 在线、文件上传和后台读取均正常后再停用 NAS 服务。
