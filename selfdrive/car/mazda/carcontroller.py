from cereal import car
from opendbc.can.packer import CANPacker
from selfdrive.car.mazda import mazdacan
from selfdrive.car.mazda.values import CarControllerParams, Buttons
from selfdrive.car.mazda.startup import IStopScheduler, TiStartupProbe, stock_ldw_allowed
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
    self.istop_scheduler = IStopScheduler()
    self.istop_first_update = True  # 标记是否是第一次update调用
    self.istop_param_read_failed = False  # 标记参数读取是否失败过

    # LDW方向盘震动控制相关
    self.ldw_vibration_enabled = False  # LDW方向盘震动是否启用
    self.ldw_vibration_last_state = False  # 上次开关状态，用于检测变化

    # Set by controlsd from current Panda safety-state reports, not ControlsReady.
    self.panda_ready = False
    self.ti_probe = TiStartupProbe()
    self.ti_detected = False  # TI是否已检测到

    # 启动时输出日志，确认代码被加载
    cloudlog.info("[I-STOP] CarController initialized, I-STOP feature loaded")
    cloudlog.info("[LDW] CarController initialized, LDW vibration feature loaded")
    cloudlog.warning("[TI] One-time startup probe: waiting for Mazda safety mode")

  def update(self, c, CS, frame):
    can_sends = []
    if CS.CP.enableTorqueInterceptor:
      previous_probe_state = self.ti_probe.state
      self.ti_probe.update(self.panda_ready, CS.ti_feedback_valid, c.active, CS.out.vEgo > 0.1)
      self.ti_detected = self.ti_probe.detected
      if self.ti_probe.state != previous_probe_state:
        cloudlog.warning(f"[TI] Startup probe {previous_probe_state} -> {self.ti_probe.state}")
      if self.ti_probe.state == 'disabled' and not c.active:
        # Select the existing stock-EPS fallback only while controls are inactive.
        # The disabled probe is latched; later feedback cannot switch TI back on.
        CS.CP.enableTorqueInterceptor = False
        cloudlog.warning("[TI] Unavailable at startup; TI disabled until controlsd restarts")

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

          # The scheduler rearms when disabled or when ECU/safety readiness is lost.
          if self.istop_enabled != prev_enabled:
            cloudlog.info(f"[I-STOP] Switch changed: {prev_enabled} -> {self.istop_enabled}")

          # 如果之前失败过，现在成功了，输出恢复日志
          if self.istop_param_read_failed:
            self.istop_param_read_failed = False
            cloudlog.info("[I-STOP] Parameter read recovered")

        except Exception as e:
          # 读取失败，保持当前状态，只在第一次失败时输出日志
          if not self.istop_param_read_failed:
            self.istop_param_read_failed = True
            cloudlog.error(f"[I-STOP] Failed to read KeepIStopDisabled: {e}")

    # Wait five continuous seconds after safety mode and vehicle ECU readiness.
    # TI feedback is deliberately not an I-stop readiness requirement.
    if self.istop_scheduler.update(self.istop_enabled, self.panda_ready and CS.istop_ready):
      can_sends.append(mazdacan.create_istop_disable_cmd())
      if self.istop_scheduler.sent == 1:
        cloudlog.info(f"[I-STOP] Started delayed burst at frame {frame}")

    apply_steer = 0
    ti_apply_steer = ti_new_steer = 0
    self.steer_rate_limited = False

    if c.active:
      # calculate steer and also set limits due to driver torque
      if CS.CP.enableTorqueInterceptor:
        if CS.ti_lkas_allowed and self.ti_detected and CS.ti_feedback_valid and self.panda_ready:
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

      # 发送 CAM_LANEINFO 到总线 0（给仪表盘/HUD）
      # Panda 会阻止转发到总线 2，避免与原车摄像头冲突
      can_sends.append(mazdacan.create_alert_command(self.packer, CS.cam_laneinfo, ldw, steer_required, self.ldw_vibration_enabled, op_active))

    # send steering command

    # Relay only the stock vibration request, never the camera's steering torque.
    # c.active is the same control gate used for steering above. No synthetic hold.
    ldw_active = stock_ldw_allowed(c.active, self.ldw_vibration_enabled, CS.cam_lkas_fresh) and bool(CS.cam_lkas.get("LDW", 0))

    # Probe once after safety readiness. A confirmed TI keeps the author's stream;
    # a failed startup probe sends nothing further, even if feedback appears later.
    if CS.CP.enableTorqueInterceptor and self.panda_ready and self.ti_probe.sending:
      ti_command = ti_apply_steer if self.ti_detected else 0
      can_sends.extend(mazdacan.create_ti_steering_control(self.packer, CS.CP.carFingerprint, frame, ti_command))
    else:
      self.ti_apply_steer_last = 0
    # always send to the stock system
    can_sends.append(mazdacan.create_steering_control(self.packer, CS.CP.carFingerprint,
                                                      frame, apply_steer, CS.cam_lkas, ldw_active))

    # EPS 0x241 is forwarded unchanged by Panda, including opaque bits/checksum.

    new_actuators = c.actuators.copy()
    new_actuators.steer = apply_steer / CarControllerParams.STEER_MAX

    return new_actuators, can_sends
