from cereal import car
from opendbc.can.packer import CANPacker
from selfdrive.car.mazda import mazdacan
from selfdrive.car.mazda.values import CarControllerParams, Buttons
from selfdrive.car import apply_std_steer_torque_limits, apply_ti_steer_torque_limits
from common.params import Params
from selfdrive.swaglog import cloudlog

VisualAlert = car.CarControl.HUDControl.VisualAlert

class CarController():
  def __init__(self, dbc_name, CP, VM):
    self.apply_steer_last = 0
    self.ti_apply_steer_last = 0
    self.packer = CANPacker(dbc_name)
    self.steer_rate_limited = False
    self.brake_counter = 0

    # I-STOP控制相关
    self.params = None  # 延迟初始化，避免启动时阻塞
    self.istop_enabled = False  # I-STOP功能是否启用
    self.istop_sent_frames = 0  # 已发送的帧数
    self.istop_max_frames = 160  # 最多发送160帧（1.6秒）
    self.istop_first_update = True  # 标记是否是第一次update调用
    self.istop_param_read_failed = False  # 标记参数读取是否失败过
    self.istop_first_param_check = True  # 标记是否是第一次参数检查

    # LDW方向盘震动控制相关
    self.ldw_vibration_enabled = False  # LDW方向盘震动是否启用
    self.ldw_vibration_last_state = False  # 上次开关状态，用于检测变化

    # LDW延时控制：确保LKAS_REQUEST值保持一段时间
    self.ldw_active_frames = 0  # LDW激活后的帧计数
    self.ldw_hold_frames = 150  # 保持150帧（1.5秒，100Hz）

    # STEER_RATE (0x241) 消息频率控制
    # 原车频率: 83Hz, CarController频率: 100Hz
    # 使用计数器实现83Hz发送: 每100帧发送83次
    self.steer_rate_counter = 0

    # TI自动检测相关
    self.ti_probe_frames = 0  # 已发送的探测帧数
    self.ti_probe_max = 10  # 最多探测10帧（100ms）
    self.ti_detected = False  # TI是否已检测到

    # 启动时输出日志，确认代码被加载
    cloudlog.info("[I-STOP] CarController initialized, I-STOP feature loaded")
    cloudlog.info("[LDW] CarController initialized, LDW vibration feature loaded")
    cloudlog.warning("[TI] TI auto-detection enabled, will probe for 10 frames")

  def update(self, c, CS, frame):
    can_sends = []

    # 第一次update时输出日志，确认update方法被调用
    if self.istop_first_update:
      self.istop_first_update = False
      cloudlog.info(f"[I-STOP] CarController.update() called for the first time at frame {frame}")

    # I-STOP控制逻辑
    # 检查是否启用I-STOP关闭功能（每100帧检查一次，约1秒）
    if frame % 100 == 0:
      # 延迟初始化Params，避免启动时阻塞
      if self.params is None:
        try:
          self.params = Params()
          cloudlog.info("[I-STOP] Params initialized successfully")
        except Exception as e:
          # 如果Params初始化失败，跳过I-STOP功能
          cloudlog.error(f"[I-STOP] Params initialization failed: {e}")

      if self.params is not None:
        try:
          prev_enabled = self.istop_enabled
          # 使用 get() 方法并检查返回值，避免参数不存在时抛出异常
          param_value = self.params.get("KeepIStopDisabled")
          self.istop_enabled = param_value == b"1" if param_value is not None else False

          # 读取LDW方向盘震动开关状态
          ldw_param_value = self.params.get("EnableLdwVibration")
          self.ldw_vibration_enabled = ldw_param_value == b"1" if ldw_param_value is not None else False

          # LDW开关状态变化时输出日志
          if self.ldw_vibration_enabled != self.ldw_vibration_last_state:
            cloudlog.info(f"[LDW] Vibration switch changed: {self.ldw_vibration_last_state} -> {self.ldw_vibration_enabled}")
            self.ldw_vibration_last_state = self.ldw_vibration_enabled

          # 开关状态变化时输出日志并重置计数器
          if self.istop_enabled != prev_enabled:
            cloudlog.info(f"[I-STOP] Switch changed: {prev_enabled} -> {self.istop_enabled}")
            if self.istop_enabled:
              self.istop_sent_frames = 0  # 重新开始计数
          # 如果开关一直是启用状态，但这是首次参数检查（启动后），也重置计数器
          elif self.istop_enabled and self.istop_first_param_check:
            self.istop_sent_frames = 0
            cloudlog.info("[I-STOP] Switch already enabled at startup, resetting counter")

          # 标记已完成首次参数检查
          if self.istop_first_param_check:
            self.istop_first_param_check = False

          # 如果之前失败过，现在成功了，输出恢复日志
          if self.istop_param_read_failed:
            self.istop_param_read_failed = False
            cloudlog.info("[I-STOP] Parameter read recovered")

        except Exception as e:
          # 读取失败，保持当前状态，只在第一次失败时输出日志
          if not self.istop_param_read_failed:
            self.istop_param_read_failed = True
            cloudlog.error(f"[I-STOP] Failed to read KeepIStopDisabled: {e}")

    # 如果功能启用，发送I-STOP关闭命令
    # 只发送160帧（1.6秒），之后停止
    if self.istop_enabled:
      if self.istop_sent_frames < self.istop_max_frames:
        # 每帧发送1次，100Hz = 10ms间隔
        can_sends.append(mazdacan.create_istop_disable_cmd())
        self.istop_sent_frames += 1

        # 输出调试日志
        if self.istop_sent_frames == 1:
          cloudlog.info(f"[I-STOP] Started sending messages at frame {frame}")
        elif self.istop_sent_frames == self.istop_max_frames:
          cloudlog.info(f"[I-STOP] Completed sending {self.istop_max_frames} messages")

    apply_steer = 0
    ti_apply_steer = ti_new_steer = 0
    self.steer_rate_limited = False

    if c.active:
      # calculate steer and also set limits due to driver torque
      if CS.CP.enableTorqueInterceptor:
        if CS.ti_lkas_allowed:
          ti_new_steer = int(round(c.actuators.steer * CarControllerParams.TI_STEER_MAX))
          ti_apply_steer = apply_ti_steer_torque_limits(ti_new_steer, self.ti_apply_steer_last,
                                                    CS.out.steeringTorque, CarControllerParams)
        # 使用TI时，CAM_LKAS的apply_steer保持为0，不请求转向
        # 这样可以避免触发原车ECU的异常检测，同时TI仍然通过CAM_LKAS2正常控制转向
        new_steer = 0  # TI模式下CAM_LKAS不请求转向
        apply_steer = 0
      else:
        # 不使用TI时，正常计算apply_steer
        new_steer = int(round(c.actuators.steer * CarControllerParams.STEER_MAX))
        apply_steer = apply_std_steer_torque_limits(new_steer, self.apply_steer_last,
                                                      CS.out.steeringTorque, CarControllerParams)

      self.steer_rate_limited = (new_steer != apply_steer) and (ti_new_steer != ti_apply_steer)

      if CS.out.standstill and frame % 5 == 0:
        # Mazda Stop and Go requires a RES button (or gas) press if the car stops more than 3 seconds
        # Send Resume button at 20hz if we're engaged at standstill to support full stop and go!
        # TODO: improve the resume trigger logic by looking at actual radar data
        can_sends.append(mazdacan.create_button_cmd(self.packer, CS.CP.carFingerprint, CS.crz_btns_counter, Buttons.RESUME))

    if c.cruiseControl.cancel or (CS.out.cruiseState.enabled and not c.enabled):
      # If brake is pressed, let us wait >70ms before trying to disable crz to avoid
      # a race condition with the stock system, where the second cancel from openpilot
      # will disable the crz 'main on'. crz ctrl msg runs at 50hz. 70ms allows us to
      # read 3 messages and most likely sync state before we attempt cancel.
      self.brake_counter = self.brake_counter + 1
      if frame % 10 == 0 and not (CS.out.brakePressed and self.brake_counter < 7):
        # Cancel Stock ACC if it's enabled while OP is disengaged
        # Send at a rate of 10hz until we sync with stock ACC state
        can_sends.append(mazdacan.create_button_cmd(self.packer, CS.CP.carFingerprint, CS.crz_btns_counter, Buttons.CANCEL))
    else:
      self.brake_counter = 0

    self.apply_steer_last = apply_steer
    self.ti_apply_steer_last = ti_apply_steer

    # send HUD alerts
    if frame % 50 == 0:
      ldw = c.hudControl.visualAlert == VisualAlert.ldw
      #steer_required = c.hudControl.visualAlert == VisualAlert.steerRequired
      # TODO: find a way to silence audible warnings so we can add more hud alerts
      #steer_required = steer_required and CS.lkas_allowed_speed
      steer_required = CS.out.steerWarning

      # 判断openpilot是否激活（engaged）
      # c.enabled 表示 openpilot 已激活，无论是横向还是纵向控制
      op_active = c.enabled

      # 调试日志：当原车LDW开关打开且openpilot未激活时，记录原车LDW状态
      if self.ldw_vibration_enabled and not op_active:
        ldw_warn = CS.cam_laneinfo.get("LDW_WARN", 0)
        if ldw_warn:
          # LDW_WARN: 0=无警告, 4=左侧偏离, 5=右侧偏离
          side = "左侧" if ldw_warn == 4 else ("右侧" if ldw_warn == 5 else f"未知({ldw_warn})")
          cloudlog.info(f"[LDW] Forwarding original camera LDW - {side}偏离, value={ldw_warn}, frame={frame}")

      # 发送 CAM_LANEINFO 到总线 0（给仪表盘/HUD）
      # Panda 会阻止转发到总线 2，避免与原车摄像头冲突
      can_sends.append(mazdacan.create_alert_command(self.packer, CS.cam_laneinfo, ldw, steer_required, self.ldw_vibration_enabled, op_active))

    # send steering command

    # 计算LDW激活状态：当开关打开、openpilot未激活、且原车摄像头检测到偏离时
    # 添加延时机制：LDW触发后保持1.5秒，确保LKAS_REQUEST值稳定传递
    ldw_active = False
    if self.ldw_vibration_enabled and not c.enabled:
      ldw_warn = CS.cam_laneinfo.get("LDW_WARN", 0)
      ldw_detected = (ldw_warn == 4 or ldw_warn == 5)  # 4=左侧偏离, 5=右侧偏离

      if ldw_detected:
        # LDW触发，重置计数器
        self.ldw_active_frames = self.ldw_hold_frames
        ldw_active = True
      elif self.ldw_active_frames > 0:
        # LDW已触发，保持激活状态直到计数器归零
        self.ldw_active_frames -= 1
        ldw_active = True
      else:
        # LDW未触发且计数器已归零
        ldw_active = False

    # TI auto-detection and sending logic
    # The TI cannot be detected unless OP sends a CAN message to it because the TI only transmits when it
    # sees the signature key in the designated address range.
    # Strategy: Send probe messages for first 10 frames (100ms), then only send if TI is detected
    if not self.ti_detected and self.ti_probe_frames < self.ti_probe_max:
      # Probing phase: send TI message with zero torque
      can_sends.extend(mazdacan.create_ti_steering_control(self.packer, CS.CP.carFingerprint, frame, 0))
      self.ti_probe_frames += 1
      if self.ti_probe_frames == self.ti_probe_max:
        cloudlog.warning(f"[TI] Probe phase completed ({self.ti_probe_max} frames sent)")
    elif self.ti_detected:
      # TI confirmed: send normal torque commands
      can_sends.extend(mazdacan.create_ti_steering_control(self.packer, CS.CP.carFingerprint, frame, ti_apply_steer))
    # else: TI not detected and probe phase done, don't send TI messages
    # always send to the stock system
    can_sends.append(mazdacan.create_steering_control(self.packer, CS.CP.carFingerprint,
                                                      frame, apply_steer, CS.cam_lkas, ldw_active))

    # 发送 STEER_RATE (0x241) 消息到 Bus 2（摄像头）
    # 转发原车EPS的STEER_RATE消息，当LDW激活时使用原车摄像头的LKAS_REQUEST值
    # 频率控制: 原车83Hz, 使用计数器实现: 每100帧发送83次
    # 算法: 每帧增加83, 当累积值>=100时发送并减去100
    self.steer_rate_counter += 83
    if self.steer_rate_counter >= 100:
      can_sends.append(mazdacan.create_steer_rate_cmd(self.packer, CS.steer_rate, CS.cam_lkas, ldw_active))
      self.steer_rate_counter -= 100

    new_actuators = c.actuators.copy()
    new_actuators.steer = apply_steer / CarControllerParams.STEER_MAX

    return new_actuators, can_sends
