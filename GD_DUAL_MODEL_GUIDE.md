# GD 双模型切换功能使用说明

## 📋 功能概述

本功能允许在两个不同版本的驾驶模型之间切换：
- **GD-0816 (Hybrid Model)** - 新版模型 (0.8.16)，默认使用
- **GD-0813 (Legacy Model)** - 旧版模型 (0.8.13)

## 🎯 模型特点对比

| 特性 | GD-0816 (Hybrid) | GD-0813 (Legacy) |
|------|------------------|------------------|
| 模型大小 | ~47MB | ~28MB |
| 性能 | 更准确，更平滑 | 较快，资源占用少 |
| 新特性 | 支持停止线检测等 | 基础功能 |
| 推荐场景 | 日常使用 | 性能受限设备 |

## 🔧 如何切换模型

### 方法1：通过UI设置（推荐）

1. 进入 **设置** → **高级** 页面
2. 找到 **"使用 GD-0813 模型"** 开关
3. **关闭** = 使用 GD-0816 (Hybrid) 模型（默认）
4. **打开** = 使用 GD-0813 (Legacy) 模型
5. **重启系统**使更改生效

### 方法2：通过命令行

```bash
# 使用 GD-0816 (Hybrid) 模型（默认）
cd /data/openpilot
python3 -c "from common.params import Params; Params().put_bool('gd_0813', False)"

# 使用 GD-0813 (Legacy) 模型
cd /data/openpilot
python3 -c "from common.params import Params; Params().put_bool('gd_0813', True)"

# 重启系统
reboot
```

## ✅ 验证当前使用的模型

重启后，检查正在运行的模型：

```bash
# 查看 modeld 进程
ps aux | grep modeld

# 输出示例：
# /data/openpilot/selfdrive/hybrid_modeld/modeld  ← 使用 GD-0816
# 或
# /data/openpilot/selfdrive/legacy_modeld/modeld  ← 使用 GD-0813
```

或者通过Python检查：

```bash
cd /data/openpilot
python3 -c "from common.params import Params; print('当前模型:', 'GD-0813 (Legacy)' if Params().get_bool('gd_0813') else 'GD-0816 (Hybrid)')"
```

## 📁 文件结构

```
openpilot/
├── selfdrive/
│   ├── hybrid_modeld/          # GD-0816 模型目录
│   │   ├── _modeld             # 编译后的二进制文件
│   │   ├── modeld              # 启动脚本
│   │   └── models/             # 模型文件
│   ├── legacy_modeld/          # GD-0813 模型目录
│   │   ├── _modeld             # 编译后的二进制文件
│   │   ├── modeld              # 启动脚本
│   │   └── models/             # 模型文件
│   └── manager/
│       ├── process_config.py   # 进程配置（包含模型切换逻辑）
│       ├── manager.py          # 管理器（包含默认参数）
│       └── init_gd_params.py   # 参数初始化脚本
└── selfdrive/ui/qt/offroad/
    └── settings.cc             # UI设置（包含切换开关）
```

## 🔄 切换流程说明

1. **用户操作**：在UI中切换开关或通过命令行设置参数
2. **参数保存**：`gd_0813` 参数被保存到 `/data/params/d/gd_0813`
3. **系统重启**：用户重启系统
4. **Manager启动**：`manager.py` 启动并读取参数
5. **进程配置**：`process_config.py` 中的 `get_model_path()` 根据参数返回正确的模型路径
6. **模型加载**：对应的 modeld 进程启动并加载模型

## ⚠️ 注意事项

1. **必须重启**：切换模型后必须重启系统才能生效
2. **性能差异**：GD-0816 模型更大，可能在某些设备上运行较慢
3. **功能差异**：GD-0816 支持更多新特性，但GD-0813更稳定
4. **默认设置**：首次启动默认使用 GD-0816 (Hybrid) 模型

## 🐛 故障排除

### 问题1：切换后模型没有变化

**解决方案**：
```bash
# 1. 确认参数已设置
cd /data/openpilot
python3 -c "from common.params import Params; print(Params().get('gd_0813'))"

# 2. 确保已重启系统
reboot

# 3. 重启后检查进程
ps aux | grep modeld
```

### 问题2：模型加载失败

**解决方案**：
```bash
# 检查模型文件是否存在
ls -la /data/openpilot/selfdrive/hybrid_modeld/_modeld
ls -la /data/openpilot/selfdrive/legacy_modeld/_modeld

# 检查权限
chmod +x /data/openpilot/selfdrive/hybrid_modeld/_modeld
chmod +x /data/openpilot/selfdrive/legacy_modeld/_modeld
```

### 问题3：UI中看不到切换开关

**解决方案**：
```bash
# 重新编译UI
cd /data/openpilot
scons -j4 selfdrive/ui/

# 重启系统
reboot
```

## 📊 性能对比测试

建议在实际使用中测试两个模型的表现：

1. **测试场景**：
   - 高速公路
   - 城市道路
   - 弯道
   - 夜间驾驶

2. **评估指标**：
   - 车道保持稳定性
   - 转向平滑度
   - 系统响应速度
   - 资源占用（CPU/GPU）

3. **选择建议**：
   - 如果设备性能充足，推荐使用 GD-0816
   - 如果遇到性能问题，可切换到 GD-0813

## 📝 更新日志

### v1.0 (2026-01-22)
- ✅ 实现双模型切换功能
- ✅ 添加UI设置开关
- ✅ 使用 GD 命名规范
- ✅ 默认使用 GD-0816 (Hybrid) 模型

## 🤝 贡献

如果您发现问题或有改进建议，请提交 Issue 或 Pull Request。

---

**注意**：本功能移植自 dragonpilot，所有 `dp` 相关命名已改为 `gd` 以区分项目。
