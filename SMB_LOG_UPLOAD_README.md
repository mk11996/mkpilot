# SMB日志上传配置说明

## 功能说明
自动将车辆数据日志上传到SMB网络共享（如Windows共享文件夹、NAS等）

## 配置步骤

### 1. 准备SMB服务器

**Windows共享文件夹：**
1. 创建一个文件夹，例如 `C:\openpilot_logs`
2. 右键 -> 属性 -> 共享 -> 高级共享
3. 勾选"共享此文件夹"
4. 设置权限（允许写入）
5. 记录共享路径，例如 `\\192.168.1.100\openpilot_logs`

**NAS（群晖/威联通等）：**
1. 创建共享文件夹
2. 设置SMB/CIFS访问权限
3. 创建用户账号并授权

### 2. 配置环境变量

编辑 `launch_env.sh` 文件，添加以下配置：

```bash
# SMB日志上传配置
export SMB_SERVER="//192.168.1.100/openpilot_logs"  # SMB服务器地址
export SMB_USERNAME="your_username"                  # SMB用户名
export SMB_PASSWORD="your_password"                  # SMB密码
export LOG_UPLOAD_INTERVAL="300"                     # 上传间隔（秒），默认300秒（5分钟）
```

**注意：**
- SMB_SERVER格式：`//IP地址/共享名称` 或 `//主机名/共享名称`
- 如果不需要密码，可以留空 `SMB_PASSWORD=""`
- 上传间隔可以根据需要调整（单位：秒）

### 3. 重启openpilot

配置完成后重启openpilot，服务会自动启动。

## 工作原理

1. **网络检测**：每30秒检查一次网络连接
2. **自动挂载**：网络可用时自动挂载SMB共享
3. **智能降级**：如果挂载失败（如缺少CIFS模块），自动切换到smbclient模式
4. **定期上传**：按设定间隔（默认5分钟）上传日志
5. **智能过滤**：只上传已完成的日志文件（至少1分钟前的文件）
6. **按日期组织**：自动在SMB服务器上按日期创建文件夹
7. **去重上传**：已存在且大小相同的文件会跳过

### 两种传输模式

**挂载模式（优先）：**
- 使用CIFS内核模块挂载SMB共享
- 传输速度快，适合大量文件
- 需要系统支持CIFS模块

**smbclient模式（降级）：**
- 使用smbclient命令行工具
- 不需要内核模块支持
- 当挂载失败时自动启用
- 适用于嵌入式系统

## 日志文件组织

上传到SMB服务器后的文件结构：
```
openpilot_logs/
├── 2026-02-14/
│   ├── vehicle_data.csv.2026-02-14--15-30-00
│   ├── vehicle_data.csv.2026-02-14--15-40-00
│   └── ...
├── 2026-02-15/
│   └── ...
```

## 查看服务状态

查看日志上传服务是否运行：
```bash
ps aux | grep log_uploader_smb
```

查看服务日志：
```bash
tail -f /data/community/crashes/log_uploader_smb.log
```

## 故障排查

### 1. 服务未启动
- 检查 `process_config.py` 中是否添加了服务
- 查看系统日志确认错误信息

### 2. 无法连接SMB
- 确认网络连接正常
- 检查SMB服务器地址、用户名、密码是否正确
- 确认防火墙允许SMB端口（445）
- 尝试手动挂载测试：
  ```bash
  mount -t cifs //192.168.1.100/openpilot_logs /tmp/test -o username=xxx,password=xxx
  ```

### 3. CIFS模块不可用
如果看到"CIFS模块不可用，将使用smbclient模式"的日志：
- 这是正常的降级行为，服务会自动切换到smbclient模式
- 确认系统已安装smbclient工具：
  ```bash
  which smbclient
  ```
- 如果未安装，可以安装：
  ```bash
  # Ubuntu/Debian
  apt-get install smbclient
  # 或者尝试加载CIFS模块
  modprobe cifs
  ```

### 4. 上传失败
- 检查SMB共享权限（需要写入权限）
- 确认磁盘空间充足
- 查看服务日志获取详细错误信息
- 如果使用smbclient模式，确认远程目录路径正确

## 安全建议

1. **使用专用账号**：为openpilot创建专用的SMB账号，限制权限
2. **网络隔离**：建议在局域网内使用，避免暴露到公网
3. **定期备份**：定期备份SMB服务器上的日志数据
4. **密码安全**：使用强密码，定期更换

## 禁用服务

如果不需要自动上传功能，可以：

1. 不设置环境变量（服务会运行但不上传）
2. 或者在 `process_config.py` 中注释掉该服务：
   ```python
   # PythonProcess("log_uploader_smb", "selfdrive.log_uploader_smb", persistent=True),
   ```

## 高级配置

### 修改上传间隔
```bash
export LOG_UPLOAD_INTERVAL="600"  # 10分钟上传一次
```

### 使用不同的挂载点
修改 `log_uploader_smb.py` 中的 `SMB_MOUNT_POINT` 变量

### 自定义日志过滤
修改 `get_log_files()` 函数中的文件筛选逻辑
