强制显示前车起步提醒UI - 测试补丁
==========================================

## 测试方法

### 方法1：使用简单UI测试（推荐，最简单）

1. 临时修改 selfdrive/ui/qt/onroad.cc，在 NvgWindow::paintGL() 函数中（约第634行），添加强制显示代码：

```cpp
void NvgWindow::paintGL() {
  CameraViewWidget::paintGL();

  UIState *s = uiState();
  if (s->worldObjectsVisible()) {
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing);
    painter.setPen(Qt::NoPen);

    drawLaneLines(painter, s->scene);

    // === 添加以下测试代码 ===
    showLeadCarAlert = true;  // 强制显示前车起步提醒
    // =========================
```

2. 重新编译UI：
```bash
cd /data/openpilot
scons -j4 selfdrive/ui/
```

3. 运行测试脚本：
```bash
python selfdrive/debug/test_lead_alert_ui.py
```

4. 你应该能立即看到前车起步提醒显示在屏幕上，可以检查：
   - 两行文字的间距（已增加20像素）
   - 半透明绿色背景大小（已上下各扩展60像素，总高度320px）
   - 文字是否完全在背景内

5. 测试完成后，记得删除强制显示的代码并重新编译

---

### 方法2：完整场景模拟测试

如果你想测试完整的触发逻辑（前车起步检测），使用这个方法：

1. 不需要修改代码

2. 运行完整场景测试脚本：
```bash
python selfdrive/debug/test_lead_alert.py
```

3. 脚本会模拟以下场景：
   - 0-10秒：前车在3米处静止
   - 10-15秒：前车开始移动并远离
   - 应该在前车移动超过2米后（约12秒时）自动触发提醒

4. 观察提醒是否正确触发和显示

---

### 方法3：使用replay回放真实数据（最真实）

如果你有之前录制的路段数据：

```bash
# 回放特定路段
tools/replay/replay <route_name>

# 例如：
# tools/replay/replay "a2a0ccea32023010|2023-07-27--13-01-19"
```

---

## 快速测试命令总结

```bash
# 步骤1：临时添加强制显示代码到 selfdrive/ui/qt/onroad.cc

# 步骤2：重新编译
cd /data/openpilot
scons -j4 selfdrive/ui/

# 步骤3：运行测试
python selfdrive/debug/test_lead_alert_ui.py

# 步骤4：观察显示效果，检查：
#   - 文字间距是否增加了20像素
#   - 绿色背景是否足够大（上下各扩展60像素）
#   - 文字是否完全在背景区域内

# 步骤5：测试完成后删除强制显示代码并重新编译
```

---

## 在Windows上的测试

如果你在Windows上开发（从路径判断），你可能需要：

1. 使用SSH连接到运行openpilot的设备（comma device或开发板）
2. 将修改后的代码同步到设备
3. 在设备上编译和运行测试

或者，如果你有WSL或虚拟机环境：

```bash
# 在WSL中编译（需要先设置好编译环境）
cd /mnt/c/Users/zgl14/Desktop/Mazda/openpilot
scons -j4 selfdrive/ui/
```

---

## 注意事项

1. **编译环境**：确保你的编译环境配置正确（需要Qt5、cereal等依赖）
2. **测试后清理**：测试完成后记得删除强制显示的测试代码
3. **设备要求**：UI测试需要图形环境支持
4. **性能**：如果设备性能不足，可能需要降低帧率

---

## 预期显示效果

修改后的前车起步提醒应该显示为：

```
┌────────────────────────────────────────┐
│                                        │  ← 上方扩展60像素
│          前车起步                      │  ← 第一行大字（32pt粗体）
│                                        │
│              ↓                         │  ← 间距增加20像素（总间距约30px）
│                                        │
│      请注意跟车距离                    │  ← 第二行小字（16pt）
│                                        │
│                                        │  ← 下方扩展60像素
└────────────────────────────────────────┘
    半透明绿色背景（总高度320px）
```

背景应该完全容纳文字，上下都有充足的空间。
