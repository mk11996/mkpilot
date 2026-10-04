#!/usr/bin/env python3
"""
前车起步提醒UI直接显示测试
直接显示前车起步提醒UI，用于测试显示效果

使用方法：
  python selfdrive/debug/test_lead_alert_ui.py

修改说明：
  在 NvgWindow::paintGL() 中临时添加：showLeadCarAlert = true;
  或者在 drawLeadCarAlert() 函数开头添加以绕过条件检查
"""
import time
from cereal import messaging, log
from selfdrive.manager.process_config import managed_processes

if __name__ == "__main__":
  print("=" * 60)
  print("前车起步提醒UI显示测试")
  print("=" * 60)
  print()
  print("提示：此脚本会启动UI并发送基础消息")
  print("要测试前车起步提醒的显示效果，需要在代码中临时强制显示：")
  print()
  print("方法1 - 在 NvgWindow::paintGL() 中添加（约第634行）：")
  print("  showLeadCarAlert = true;  // 强制显示测试")
  print()
  print("方法2 - 直接调用绘制函数测试布局")
  print()
  print("按 Ctrl+C 退出")
  print("=" * 60)
  print()

  # 启动必要的进程
  procs = ['camerad', 'ui', 'modeld', 'calibrationd']

  for p in procs:
    managed_processes[p].start()

  # 创建消息发布器
  pm = messaging.PubMaster([
    'controlsState',
    'deviceState',
    'pandaStates',
    'carParams',
    'carState',
  ])

  # 初始化基础消息
  msgs = {s: messaging.new_message(s) for s in [
    'controlsState',
    'deviceState',
    'carParams',
    'carState'
  ]}

  # 设备状态
  msgs['deviceState'].deviceState.started = True

  # 车辆参数
  msgs['carParams'].carParams.openpilotLongitudinalControl = True

  # Panda状态
  msgs['pandaStates'] = messaging.new_message('pandaStates', 1)
  msgs['pandaStates'].pandaStates[0].ignitionLine = True
  msgs['pandaStates'].pandaStates[0].pandaType = log.PandaState.PandaType.uno

  # 控制状态
  msgs['controlsState'].controlsState.enabled = False
  msgs['controlsState'].controlsState.engageable = True
  msgs['controlsState'].controlsState.vCruise = 0

  # 车辆状态（静止）
  msgs['carState'].carState.vEgo = 0.0
  msgs['carState'].carState.cruiseState.enabled = False

  print("UI已启动，持续发送消息中...")
  print("如果已添加强制显示代码，应该能看到前车起步提醒")
  print()

  try:
    while True:
      time.sleep(1 / 100)  # 100 Hz
      for s in msgs:
        pm.send(s, msgs[s])

  except KeyboardInterrupt:
    print("\n正在退出...")
    for p in procs:
      managed_processes[p].stop()
    print("测试结束")
