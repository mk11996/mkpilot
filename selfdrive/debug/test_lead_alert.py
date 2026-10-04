#!/usr/bin/env python3
"""
前车起步提醒UI测试工具
无需连接panda即可测试前车起步提醒的UI显示效果

使用方法：
  python selfdrive/debug/test_lead_alert.py

按 Ctrl+C 退出
"""
import time
import math
from cereal import messaging, log
from selfdrive.manager.process_config import managed_processes

if __name__ == "__main__":
  print("启动UI测试模式...")
  print("将模拟前车起步场景来测试UI显示效果")
  print("按 Ctrl+C 退出\n")

  # 启用前车起步提醒功能
  from common.params import Params
  params = Params()
  params.put_bool("LeadCarAlertToggle", True)
  print("已启用前车起步提醒功能 (LeadCarAlertToggle = True)\n")

  # 只启动UI进程，不启动modeld和camerad（它们会发送冲突消息）
  # 我们将手动发送所有需要的消息
  procs = ['ui']

  for p in procs:
    managed_processes[p].start()

  # 创建消息发布器
  pm = messaging.PubMaster([
    'deviceState',
    'pandaStates',
    'carParams',
    'carState',
    'modelV2',
    'radarState'
  ])

  print("=" * 60)
  print("测试场景：")
  print("1. 初始稳定期（0-5秒）- 让数据稳定")
  print("2. 前车在前方3米处静止（5-26秒）- 等待21秒停止时间")
  print("3. 前车开始移动并远离（26-31秒）")
  print("4. 应该在前车移动超过2米后触发提醒（约28秒时）")
  print()
  print("⏱️  请耐心等待约28秒后观察提醒显示...")
  print("=" * 60)
  print()

  try:
    start_time = time.time()
    frame_count = 0

    while True:
      current_time = time.time() - start_time
      frame_count += 1

      # 模拟前车场景
      # 0-5秒：初始稳定期（让 lead_stable_count 累积）
      # 5-26秒：前车在3米处静止（21秒 > 20秒阈值）
      # 26-31秒：前车开始移动，从3米逐渐远离到6米
      # 约28秒时：移动距离超过2米，触发提醒

      if current_time < 5:
        # 阶段0：初始稳定期
        lead_distance = 3.0 + 1.52  # 加上摄像头偏移
        lead_velocity = 0.0
        stage = "初始稳定期"
      elif current_time < 26:
        # 阶段1：前车静止在3米处（等待21秒 > 20秒）
        lead_distance = 3.0 + 1.52  # 加上摄像头偏移
        lead_velocity = 0.0
        elapsed = current_time - 5
        stage = f"前车静止中（已{elapsed:.0f}秒/需21秒）"
      elif current_time < 31:
        # 阶段2：前车开始移动（起步）
        progress = (current_time - 26) / 5.0  # 0-1
        lead_distance = 3.0 + progress * 3.0 + 1.52  # 从3米移动到6米（加摄像头偏移）
        lead_velocity = 2.0  # 前车速度2 m/s
        moved = progress * 3.0
        stage = f"前车起步中！(已移动{moved:.1f}米)"
      else:
        # 阶段3：前车继续远离
        lead_distance = 6.0 + (current_time - 31) * 2.0 + 1.52
        lead_velocity = 2.0
        stage = "前车远离中"

      # ===== 每次循环创建新消息，避免重复写入警告 =====

      # 设备状态
      msg_device = messaging.new_message('deviceState')
      msg_device.deviceState.started = True

      # Panda状态
      msg_panda = messaging.new_message('pandaStates', 1)
      msg_panda.pandaStates[0].ignitionLine = True
      msg_panda.pandaStates[0].pandaType = log.PandaState.PandaType.uno

      # 车辆参数
      msg_car_params = messaging.new_message('carParams')
      msg_car_params.carParams.openpilotLongitudinalControl = True

      # 车辆状态（静止）
      msg_car_state = messaging.new_message('carState')
      msg_car_state.carState.vEgo = 0.0
      msg_car_state.carState.cruiseState.enabled = False

      # 创建modelV2消息（包含前车数据）
      msg_model = messaging.new_message('modelV2')
      model = msg_model.modelV2

      # 设置模型执行时间（只设置必要的字段）
      model.frameId = frame_count
      model.modelExecutionTime = 0.05  # 50ms

      # 设置路径数据（position）
      position = model.position
      position.init('x', 33)
      position.init('y', 33)
      position.init('z', 33)
      position.init('xStd', 33)
      position.init('yStd', 33)
      position.init('zStd', 33)
      for i in range(33):
        position.x[i] = i * 2.0  # 0到64米
        position.y[i] = 0.0
        position.z[i] = 0.0
        position.xStd[i] = 0.1
        position.yStd[i] = 0.1
        position.zStd[i] = 0.1

      # 设置前车数据（leadsV3）
      # leadsV3是一个列表，需要初始化3个元素
      model.init('leadsV3', 3)
      leads = model.leadsV3

      # 设置第一辆前车（最近的）
      lead = leads[0]
      lead.prob = 0.9  # 高置信度
      lead.init('x', 3)
      lead.x[0] = lead_distance
      lead.x[1] = lead_distance + 10
      lead.x[2] = lead_distance + 20
      lead.init('y', 3)
      lead.y[0] = 0.0
      lead.y[1] = 0.0
      lead.y[2] = 0.0
      lead.init('v', 3)
      lead.v[0] = lead_velocity
      lead.v[1] = lead_velocity
      lead.v[2] = lead_velocity
      lead.init('a', 3)
      lead.a[0] = 0.0
      lead.a[1] = 0.0
      lead.a[2] = 0.0
      lead.init('xStd', 3)
      lead.xStd[0] = 0.1
      lead.xStd[1] = 0.2
      lead.xStd[2] = 0.3
      lead.init('yStd', 3)
      lead.yStd[0] = 0.1
      lead.yStd[1] = 0.2
      lead.yStd[2] = 0.3
      lead.init('vStd', 3)
      lead.vStd[0] = 0.1
      lead.vStd[1] = 0.2
      lead.vStd[2] = 0.3
      lead.init('aStd', 3)
      lead.aStd[0] = 0.1
      lead.aStd[1] = 0.2
      lead.aStd[2] = 0.3

      # 其他前车设为无效
      for i in range(1, 3):
        leads[i].prob = 0.0
        leads[i].init('x', 3)
        leads[i].init('y', 3)
        leads[i].init('v', 3)
        leads[i].init('a', 3)
        leads[i].init('xStd', 3)
        leads[i].init('yStd', 3)
        leads[i].init('vStd', 3)
        leads[i].init('aStd', 3)

      # 创建雷达消息
      msg_radar = messaging.new_message('radarState')
      radar = msg_radar.radarState
      radar.leadOne.dRel = lead_distance - 1.52  # 减去摄像头偏移
      radar.leadOne.vRel = lead_velocity
      radar.leadOne.aRel = 0.0
      radar.leadOne.status = True

      # 每秒打印一次状态
      if frame_count % 20 == 0:
        print(f"[{current_time:5.1f}s] {stage} | "
              f"距离: {lead_distance-1.52:.1f}m | "
              f"速度: {lead_velocity:.1f}m/s")

      # 发送所有消息
      pm.send('deviceState', msg_device)
      pm.send('pandaStates', msg_panda)
      pm.send('carParams', msg_car_params)
      pm.send('carState', msg_car_state)
      pm.send('modelV2', msg_model)
      pm.send('radarState', msg_radar)

      time.sleep(1 / 20)  # 20 Hz

  except KeyboardInterrupt:
    print("\n正在退出...")
    for p in procs:
      managed_processes[p].stop()
    print("测试结束")
