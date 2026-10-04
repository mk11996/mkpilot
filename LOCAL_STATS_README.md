# 本地行程统计功能说明

## ✅ 已完成的功能

### 1. 本地统计模块 (`statsd_local.py`)

**功能：**
- 实时统计驾驶距离、时间、行程数
- 离线也能查看统计数据
- 自动过滤短暂启动（距离<100米或时长<10秒）
- 周统计自动重置（每7天）

**统计数据：**
- **ALL TIME（总计）**：所有历史行程
- **PAST WEEK（过去一周）**：最近7天的行程

**数据存储：**
- `StatsAllTime`：总统计数据
- `StatsWeek`：周统计数据
- `StatsLastReset`：上次重置时间

### 2. DriveStats UI 改进

**改进：**
- 优先显示本地数据（离线可用）
- 有网络时自动同步云端数据
- 每次打开主界面自动刷新显示

## 📊 数据格式

```json
{
  "routes": 15,          // 行程数
  "distance": 150000.0,  // 总距离（米）
  "minutes": 120.5       // 总时长（分钟）
}
```

## 🚀 使用方法

### 启动统计进程

统计进程已添加到系统进程列表，会随 openpilot 自动启动：
```bash
# 进程名：statsd_local
# 位置：selfdrive/statsd_local.py
```

### 查看统计数据

1. **通过 UI 界面**：停车后自动显示在离线主界面
2. **通过命令行**：
```bash
# 查看总统计
cat /data/params/d/StatsAllTime

# 查看周统计
cat /data/params/d/StatsWeek
```

### 手动重置统计

```bash
# 重置总统计
rm /data/params/d/StatsAllTime

# 重置周统计
rm /data/params/d/StatsWeek

# 重置时间戳
rm /data/params/d/StatsLastReset
```

## 🔧 技术细节

### 距离计算方法

使用**梯形积分法**计算距离，更准确：
```python
distance_increment = ((v_ego + last_v_ego) / 2.0) * dt
```

### 数据更新频率

- 统计更新：**20Hz**（每50ms）
- UI 刷新：每次打开离线界面

### 单位转换

- 存储单位：**米**
- 显示单位：
  - 公制：**公里**（米 ÷ 1000）
  - 英制：**英里**（公里 × 0.621371）

## ⚠️ 注意事项

1. **短暂启动过滤**
   - 距离 < 100 米：不记录
   - 时长 < 10 秒：不记录

2. **周统计重置**
   - 每 7 天自动重置
   - 保留历史总统计

3. **离线 vs 联网**
   - 离线：只显示本地数据
   - 联网：本地数据 + 云端同步（云端优先）

## 🐛 故障排查

### 问题：统计数据不更新

检查：
```bash
# 1. 确认进程运行
ps aux | grep statsd_local

# 2. 查看日志
tail -f /data/community/crashes/statsd_local

# 3. 手动启动测试
cd /data/openpilot
python selfdrive/statsd_local.py
```

### 问题：数据显示为 0

原因：
- 首次使用没有历史数据
- 还没有完成一次行程
- 参数存储被清除

解决：
- 正常行驶一次即可开始统计
