# openpilot 修改清单

## 必改文件

```text
common/api/__init__.py
selfdrive/athena/registration.py
selfdrive/athena/athenad.py
selfdrive/loggerd/uploader.py
selfdrive/manager/process_config.py
```

## 具体修改

### API 地址

将 `API_HOST` 指向自建服务器。

### Athena 地址

将 `ATHENA_HOST` 指向：

```text
wss://你的域名/ws
```

代码会继续拼接：

```text
/v2/{device_id}
```

### 注册

删除或旁路官方 `pilotauth` 注册逻辑，改为一次性注册码注册。

注册成功后写入：

```text
DongleId
```

同时确保设备本地 RSA 私钥不上传服务器。

### 上传器

保留：

- 原有上传队列；
- xattr 上传标记；
- 失败重试；
- 网络类型判断；
- 上传优先级。

增加：

```text
/data/logs/
```

作为自定义 CSV 的扫描目录。

### 进程配置

迁移完成后：

```python
manage_athenad enabled=True
uploader enabled=True
log_uploader_smb disabled
```

## 测试顺序

1. 只测试设备注册；
2. 只测试 `qlog.bz2` 上传；
3. 测试断网重试；
4. 测试自定义 CSV；
5. 测试 Athena 只读 RPC；
6. 最后测试重启命令。

## 禁止事项

不要在未完成本地安全验证前：

- 开放远程车辆控制；
- 允许服务器直接写车辆控制参数；
- 删除原始本地日志；
- 关闭 openpilot 本地安全检查。

