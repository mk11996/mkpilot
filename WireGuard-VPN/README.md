# 个人 WireGuard VPN（独立 Docker 项目）

本项目与 openpilot 公网服务完全独立：不修改现有 Compose、不接入 Caddy、不使用 80/443，也不访问 openpilot 的数据库或文件。

## 部署前确认

- 服务器已确认 `/dev/net/tun` 可用，且 WireGuard 内核模块可加载。
- 在阿里云轻量服务器控制台的**防火墙**中添加一条规则：`UDP`、端口 `51820`、来源 `0.0.0.0/0`。不要放行“全部 UDP”。
- 保留现有的 SSH 22、网站 80/443 防火墙规则不变。

## 服务器部署

将整个本目录上传至服务器后，使用独立目录：

```bash
mkdir -p /home/admin/wireguard-vpn
cd /home/admin/wireguard-vpn
cp .env.example .env
vi .env
```

在 `.env` 中至少确认：

- `WG_ENDPOINT=www.39love.net`
- `WG_PORT=51820`
- `WG_PEERS=phone,laptop`（按实际客户端名称修改）
- `PUID` 与 `PGID`：在服务器执行 `id -u; id -g` 后填入输出值。

首次启动：

```bash
sudo docker compose -p personal-wireguard up -d
sudo docker compose -p personal-wireguard ps
sudo docker compose -p personal-wireguard logs --tail 100 wireguard
```

## 导入手机和电脑

首次启动后，每个客户端都会生成一份私有配置文件：

```bash
sudo ls -l config/peer_phone/peer_phone.conf
sudo ls -l config/peer_laptop/peer_laptop.conf
```

用 SFTP/SCP 将相应 `.conf` 文件安全复制到对应设备，再通过官方 WireGuard 客户端“从文件导入”。配置文件含私钥，不要发送到聊天群、邮件或公开网盘。

连接后可在客户端浏览器访问一个“查看本机 IP”的网站，确认显示为服务器公网 IP。若无法连接，依次检查：阿里云 UDP 51820 防火墙、服务器系统防火墙、容器状态和客户端配置中的 `Endpoint`。

## 日常管理

查看状态：

```bash
sudo docker compose -p personal-wireguard exec wireguard wg show
```

停止 VPN（不影响 openpilot）：

```bash
sudo docker compose -p personal-wireguard down
```

更新镜像：

```bash
sudo docker compose -p personal-wireguard pull
sudo docker compose -p personal-wireguard up -d
```

## 添加或移除设备

添加设备时，将新名称加入 `.env` 的 `WG_PEERS`，再重建容器。遗失设备应从 WireGuard peer 配置中撤销，不要继续使用旧配置。

## 安全边界

- 仅使用个人设备，不向他人提供服务。
- 每台设备单独使用自己的配置文件；不要多人共用同一个 peer。
- 不部署公网 VPN 管理网页，避免额外攻击面。
- 这会与 openpilot 共用服务器出口带宽和流量额度；大量观看驾驶视频时 VPN 速度可能下降。
