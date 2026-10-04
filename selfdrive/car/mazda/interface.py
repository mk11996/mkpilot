#!/usr/bin/env python3
from cereal import car
from common.params import Params
from selfdrive.config import Conversions as CV
from selfdrive.car.mazda.values import CAR, LKAS_LIMITS
from selfdrive.car import STD_CARGO_KG, scale_rot_inertia, scale_tire_stiffness, gen_empty_fingerprint, get_safety_config
from selfdrive.car.interfaces import CarInterfaceBase
from selfdrive import global_ti as TI
from selfdrive.swaglog import cloudlog

ButtonType = car.CarState.ButtonEvent.Type
EventName = car.CarEvent.EventName
GearShifter = car.CarState.GearShifter  # 用于检测挂P档

class CarInterface(CarInterfaceBase):

  def __init__(self, CP, CarController, CarState):
    super().__init__(CP, CarController, CarState)
    # 初始化 Params 对象
    self.params = Params()

    # 方案3改进版：
    # - LateralOnlyControl: 设置中的总开关（是否允许使用仅横向模式）
    # - LateralOnlyActive: UI按钮点击后设置的激活状态
    # - Panda根据启动时的LateralOnlyControl配置安全策略

    # 记录启动时的配置（用于检测运行时配置变化）
    self.lateral_only_control_at_init = self.params.get_bool("LateralOnlyControl")

    # 启动时清除激活状态（确保每次启动都是干净的状态）
    if self.params.get_bool("LateralOnlyActive"):
      self.params.delete("LateralOnlyActive")
      cloudlog.info("Cleared LateralOnlyActive parameter on startup")

    # 用于跟踪仅横向模式的激活状态变化
    self.lateral_only_active_last = False

    # LDW方向盘震动开关缓存（避免每帧读取参数文件）
    self.ldw_vibration_enabled = self.params.get("EnableLdwVibration") == b"1"

    # TI自动检测状态跟踪
    self.ti_detection_done = False  # TI检测是否已完成

  @staticmethod
  def compute_gb(accel, speed):
    return float(accel) / 4.0

  @staticmethod
  def get_params(candidate, fingerprint=gen_empty_fingerprint(), car_fw=None):
    print("in get_params, entering get_std_params")
    ret = CarInterfaceBase.get_std_params(candidate, fingerprint)

    ret.carName = "mazda"

    # 方案3：根据用户设置决定是否允许仅横向模式
    # param bit 0: 0 = 只允许完整控制模式（需要ACC）
    #              1 = 允许仅横向模式（Panda自动根据ACC状态切换）
    # 读取用户设置
    from common.params import Params
    lateral_only_control_enabled = Params().get_bool("LateralOnlyControl")
    safety_param = 1 if lateral_only_control_enabled else 0

    ret.safetyConfigs = [get_safety_config(car.CarParams.SafetyModel.mazda, safety_param)]
    ret.radarOffCan = False  # 启用雷达，处理 CAN 消息 865

    ret.dashcamOnly = False # candidate not in (CAR.CX5_2022, CAR.CX9_2021)

    # Enable TI support by default for auto-detection
    # Will be disabled at runtime if TI doesn't respond
    ret.enableTorqueInterceptor = True

    ret.steerActuatorDelay = 0.1
    ret.steerRateCost = 1.0
    ret.steerLimitTimer = 0.8
    tire_stiffness_factor = 0.70   # not optimized yet

    if ret.enableTorqueInterceptor:
      print("Adjusting PID parameters for TI")
      if candidate in (CAR.CX5, CAR.CX5_2022):
        ret.mass = 3655 * CV.LB_TO_KG + STD_CARGO_KG
        ret.wheelbase = 2.7
        ret.steerRatio = 15.5
        ret.lateralTuning.pid.kiBP = [5.0, 25.0]
        ret.lateralTuning.pid.kpBP = [5.0, 25.0]
        ret.lateralTuning.pid.kpV = [0.25,0.28]
        ret.lateralTuning.pid.kiV = [0.01,0.025]
        ret.lateralTuning.pid.kf = 0.00008

        ret.lateralTuning.init('indi')
        ret.lateralTuning.indi.innerLoopGainBP = [5.0, 35]
        ret.lateralTuning.indi.innerLoopGainV = [4.5, 6.0]
        ret.lateralTuning.indi.outerLoopGainBP = [5, 35]
        ret.lateralTuning.indi.outerLoopGainV = [3.0, 6]
        ret.lateralTuning.indi.timeConstantBP = [2, 35]
        ret.lateralTuning.indi.timeConstantV = [0.2, 1.5]
        ret.lateralTuning.indi.actuatorEffectivenessBP = [0, 25]
        ret.lateralTuning.indi.actuatorEffectivenessV = [2.0, 1]

      elif candidate in [CAR.CX9, CAR.CX9_2021]:
        ret.mass = 4217 * CV.LB_TO_KG + STD_CARGO_KG
        ret.wheelbase = 3.1
        ret.steerRatio = 17.6
        ret.lateralTuning.pid.kiBP = [8.0, 30.0]
        ret.lateralTuning.pid.kpBP = [8.0, 30.0]
        ret.lateralTuning.pid.kpV = [0.10,0.22]
        ret.lateralTuning.pid.kiV = [0.01,0.019]
        ret.lateralTuning.pid.kf = 0.00006
      elif candidate == CAR.MAZDA3:
        ret.mass = 2875 * CV.LB_TO_KG + STD_CARGO_KG
        ret.wheelbase = 2.7
        ret.steerRatio = 14.0

        ret.lateralTuning.pid.kiBP = [5.0, 25.0]
        ret.lateralTuning.pid.kpBP = [5.0, 25.0]
        ret.lateralTuning.pid.kpV = [0.25,0.28]
        ret.lateralTuning.pid.kiV = [0.01,0.025]
        ret.lateralTuning.pid.kf = 0.00008

        ret.lateralTuning.init('indi')
        ret.lateralTuning.indi.innerLoopGainBP = [5.0, 35]
        ret.lateralTuning.indi.innerLoopGainV = [4.5, 6.0]
        ret.lateralTuning.indi.outerLoopGainBP = [5, 35]
        ret.lateralTuning.indi.outerLoopGainV = [3.0, 6]
        ret.lateralTuning.indi.timeConstantBP = [2, 35]
        ret.lateralTuning.indi.timeConstantV = [0.2, 1.5]
        ret.lateralTuning.indi.actuatorEffectivenessBP = [0, 25]
        ret.lateralTuning.indi.actuatorEffectivenessV = [2.0, 1]
      elif candidate == CAR.MAZDA6:
        ret.mass = 3443 * CV.LB_TO_KG + STD_CARGO_KG
        ret.wheelbase = 2.83
        ret.steerRatio = 15.5
        ret.lateralTuning.pid.kiBP = [8.0, 30.0]
        ret.lateralTuning.pid.kpBP = [8.0, 30.0]
        ret.lateralTuning.pid.kpV = [0.10,0.22]
        ret.lateralTuning.pid.kiV = [0.01,0.019]
        ret.lateralTuning.pid.kf = 0.00006
    else:
      if candidate in (CAR.CX5, CAR.CX5_2022):
        ret.mass = 3655 * CV.LB_TO_KG + STD_CARGO_KG
        ret.wheelbase = 2.7
        ret.steerRatio = 15.5
        ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[0.], [0.]]
        ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.19], [0.019]]
        ret.lateralTuning.pid.kf = 0.00006
      elif candidate in [CAR.CX9, CAR.CX9_2021]:
        ret.mass = 4217 * CV.LB_TO_KG + STD_CARGO_KG
        ret.wheelbase = 3.1
        ret.steerRatio = 17.6
        ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[0.], [0.]]
        ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.19], [0.019]]
        ret.lateralTuning.pid.kf = 0.00006
      elif candidate == CAR.MAZDA3:
        ret.mass = 2875 * CV.LB_TO_KG + STD_CARGO_KG
        ret.wheelbase = 2.7
        ret.steerRatio = 14.0
        ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[0.], [0.]]
        ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.19], [0.019]]
        ret.lateralTuning.pid.kf = 0.00006
      elif candidate == CAR.MAZDA6:
        ret.mass = 3443 * CV.LB_TO_KG + STD_CARGO_KG
        ret.wheelbase = 2.83
        ret.steerRatio = 15.5
        ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[0.], [0.]]
        ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.19], [0.019]]
        ret.lateralTuning.pid.kf = 0.00006

    if candidate not in (CAR.CX5_2022, ):
      ret.minSteerSpeed = LKAS_LIMITS.DISABLE_SPEED * CV.KPH_TO_MS

    ret.centerToFront = ret.wheelbase * 0.41

    # TODO: get actual value, for now starting with reasonable value for
    # civic and scaling by mass and wheelbase
    ret.rotationalInertia = scale_rot_inertia(ret.mass, ret.wheelbase)

    # TODO: start from empirically derived lateral slip stiffness for the civic and scale by
    # mass and CG position, so all cars will have approximately similar dyn behaviors
    ret.tireStiffnessFront, ret.tireStiffnessRear = scale_tire_stiffness(ret.mass, ret.wheelbase, ret.centerToFront,
                                                                         tire_stiffness_factor=tire_stiffness_factor)

    return ret

  # returns a car.CarState
  def update(self, c, can_strings):

    self.cp.update_strings(can_strings)
    self.cp_cam.update_strings(can_strings)
    self.cp_body.update_strings(can_strings)

    # TI auto-detection: check if TI is responding after probe phase completes
    if self.CP.enableTorqueInterceptor and not self.ti_detection_done and self.CC.ti_probe_frames >= self.CC.ti_probe_max:
      # Probe phase completed, check if we're receiving TI_FEEDBACK messages
      # Wait a few more frames after probe to ensure messages have time to arrive
      if self.frame > self.CC.ti_probe_max + 5:
        self.ti_detection_done = True
        if self.cp_body.can_valid:
          self.CC.ti_detected = True
          cloudlog.warning("[TI] TI detected via feedback messages, TI support enabled")
        else:
          # No TI response, disable TI support
          self.CP.enableTorqueInterceptor = False
          cloudlog.warning("[TI] No TI response after probe phase, TI support disabled")

    if self.CP.enableTorqueInterceptor and not TI.enabled:
      TI.enabled = True
      self.cp_body = self.CS.get_body_can_parser(self.CP)
    ret = self.CS.update(self.cp, self.cp_cam, self.cp_body)

    # 检查发动机是否运行（通过 START_STOP 信号判断）
    # START_STOP: 0=关闭, 1=部分通电, 2=全车通电（发动机运行）
    start_stop = self.cp.vl["MSG_04"]["START_STOP"]
    engine_running = start_stop == 2  # 2 表示全车通电，发动机运行

    # 记录 START_STOP 状态变化
    if not hasattr(self, 'start_stop_prev'):
      self.start_stop_prev = start_stop
      cloudlog.warning(f"[Ignition] Initial START_STOP={start_stop}, engine_running={engine_running}")
    elif start_stop != self.start_stop_prev:
      cloudlog.warning(f"[Ignition] START_STOP changed: {self.start_stop_prev} -> {start_stop}, engine_running={engine_running}")
      self.start_stop_prev = start_stop

    # 熄火时强制 canValid = true，避免显示 CAN 错误
    # 车辆熄火后 CAN 消息减少，但这不是真正的 CAN 错误
    if engine_running:
      # TI feedback is safety-critical only while the optional interceptor is enabled.
      # Without TI, cp_body has no required messages and must not create a false CAN error.
      pt_can_valid = self.cp.can_valid
      cam_can_valid = self.cp_cam.can_valid
      body_can_valid = self.cp_body.can_valid if self.cp_body is not None else True
      ret.canValid = pt_can_valid and cam_can_valid and (not self.CP.enableTorqueInterceptor or body_can_valid)
      can_detail = (pt_can_valid, cam_can_valid, body_can_valid, bool(self.CP.enableTorqueInterceptor))
      if getattr(self, "_last_can_valid_detail", None) != can_detail:
        cloudlog.warning(f"[CAN] validity pt={pt_can_valid} cam={cam_can_valid} body={body_can_valid} ti_enabled={self.CP.enableTorqueInterceptor} result={ret.canValid}")
        self._last_can_valid_detail = can_detail
    else:
      ret.canValid = True  # 熄火时忽略 CAN 检查

    # TI disconnect detection: if TI was detected but cp_body becomes invalid, disable TI
    if self.CC.ti_detected and self.CP.enableTorqueInterceptor and not self.cp_body.can_valid:
      if not hasattr(self, 'ti_invalid_frames'):
        self.ti_invalid_frames = 0
      self.ti_invalid_frames += 1
      # If TI messages missing for 50 frames (500ms), consider it disconnected
      if self.ti_invalid_frames > 50:
        self.CP.enableTorqueInterceptor = False
        self.CC.ti_detected = False
        cloudlog.warning("[TI] TI disconnected (no feedback for 500ms), TI support disabled")
    elif self.cp_body.can_valid:
      self.ti_invalid_frames = 0

    # 方案3改进版：读取UI按钮设置的激活状态
    # 用户通过UI按钮点击来激活/停用仅横向模式
    lateral_only_active = self.params.get_bool("LateralOnlyActive")

    # 记录参数状态变化（用于调试按钮问题）
    if not hasattr(self, 'lateral_only_active_logged'):
      self.lateral_only_active_logged = lateral_only_active
      cloudlog.info(f"[Mode][DEBUG] Initial state: lateral_only_active={lateral_only_active}, ACC_enabled={ret.cruiseState.enabled}")
    elif lateral_only_active != self.lateral_only_active_logged:
      cloudlog.info(f"[Mode][DEBUG] LateralOnlyActive changed: {self.lateral_only_active_logged} -> {lateral_only_active}, ACC_enabled={ret.cruiseState.enabled}")
      self.lateral_only_active_logged = lateral_only_active

    # 事件驱动日志：记录关键状态变化（不使用固定间隔）
    if not hasattr(self, 'last_gear'):
      self.last_gear = ret.gearShifter
    if not hasattr(self, 'last_v_ego'):
      self.last_v_ego = ret.vEgo
    if not hasattr(self, 'last_brake'):
      self.last_brake = ret.brake

    # 只在关键状态变化时记录
    gear_changed = (ret.gearShifter != self.last_gear)
    speed_changed_significantly = abs(ret.vEgo - self.last_v_ego) > 5.0  # 速度变化超过5m/s
    brake_changed_significantly = abs(ret.brake - self.last_brake) > 0.3  # 刹车压力变化超过30%

    if gear_changed or speed_changed_significantly or brake_changed_significantly:
      cloudlog.info(f"[Mode][DEBUG] State change: gear={str(ret.gearShifter)}, v_ego={ret.vEgo:.1f}m/s, brake={ret.brake:.2f}, lateral_only={lateral_only_active}, ACC={ret.cruiseState.enabled}")
      self.last_gear = ret.gearShifter
      self.last_v_ego = ret.vEgo
      self.last_brake = ret.brake

    # === 配置一致性检查 ===
    # 检测用户是否在运行时修改了LateralOnlyControl设置
    lateral_only_control_current = self.params.get_bool("LateralOnlyControl")
    if lateral_only_control_current != self.lateral_only_control_at_init:
      # 配置已改变，但Panda使用的是启动时的配置
      if lateral_only_active:
        # 用户想使用仅横向模式，但配置不匹配
        if not self.lateral_only_control_at_init:
          # Panda不允许仅横向模式，强制清除激活状态
          self.params.delete("LateralOnlyActive")
          lateral_only_active = False
          cloudlog.warning("LateralOnlyControl enabled at runtime but Panda not configured, cleared active state")

    # === 权限检查 ===
    # 如果当前设置不允许仅横向模式，但激活状态为true，强制清除
    if not lateral_only_control_current and lateral_only_active:
      self.params.delete("LateralOnlyActive")
      lateral_only_active = False
      cloudlog.warning("LateralOnlyControl disabled, clearing active state")

    # === 模式冲突检测 ===
    # 如果ACC已启动，不允许激活仅横向模式（避免两种模式冲突）
    if lateral_only_active and ret.cruiseState.enabled:
      self.params.delete("LateralOnlyActive")
      lateral_only_active = False
      cloudlog.warning("[Mode][DEBUG] Lateral-only mode cleared: ACC is active, switching to full control mode")

    # === 自动退出条件（安全保护）===
    # 这些条件下自动清除参数，确保安全
    if lateral_only_active:
      # 1. 挂P档：车辆停止，不需要转向控制
      if ret.gearShifter == GearShifter.park:
        self.params.delete("LateralOnlyActive")
        lateral_only_active = False
        cloudlog.info("[Mode] Lateral-only mode cleared: vehicle in PARK")

      # 2. 非D档（R档、N档等）：不应该在这些档位使用转向辅助
      elif ret.gearShifter != GearShifter.drive:
        self.params.delete("LateralOnlyActive")
        lateral_only_active = False
        cloudlog.warning(f"[Mode] Lateral-only mode cleared: not in DRIVE gear (gear={str(ret.gearShifter)})")

      # 3. 紧急刹车：刹车压力 > 70%，驾驶员正在主动干预
      elif ret.brake > 0.7:
        self.params.delete("LateralOnlyActive")
        lateral_only_active = False
        cloudlog.warning(f"[Mode] Lateral-only mode cleared: emergency brake (brake={ret.brake:.2f})")

      # 4. 车速过高：超过 120 km/h，应使用完整控制模式
      elif ret.vEgo > 120.0 * CV.KPH_TO_MS:
        self.params.delete("LateralOnlyActive")
        lateral_only_active = False
        cloudlog.warning(f"[Mode] Lateral-only mode cleared: speed too high (vEgo={ret.vEgo:.1f} m/s)")

    # events
    events = self.create_common_events(ret, lateral_only=lateral_only_active)

    if self.CS.lkas_disabled:
      events.add(EventName.lkasDisabled)

    # 原车LDW触发openpilot警告：
    # 当EnableLdwVibration开关打开且openpilot未激活时（含ACC和仅横向模式），
    # 原车摄像头检测到车道偏离，同步触发openpilot的LDW警告（屏幕+声音）
    # 每100帧更新一次参数，避免每帧读取文件
    if self.frame % 100 == 0:
      self.ldw_vibration_enabled = self.params.get("EnableLdwVibration") == b"1"
    op_engaged = ret.cruiseState.enabled or lateral_only_active
    if self.ldw_vibration_enabled and not op_engaged:
      if self.CS.cam_ldw_left or self.CS.cam_ldw_right:
        events.add(EventName.ldw)

    # 仅横向模式：处理原车ACC取消按钮
    # 当用户按下取消按钮时，清除仅横向模式参数并立即生成取消事件
    if lateral_only_active:
      for b in ret.buttonEvents:
        if b.type == ButtonType.cancel and b.pressed:
          self.params.delete("LateralOnlyActive")
          lateral_only_active = False  # 立即更新状态
          events.add(EventName.buttonCancel)
          cloudlog.info("Lateral-only mode: cleared by cancel button, generating buttonCancel event")
          break

    # 仅横向模式：检测激活状态变化，生成 buttonEnable/buttonCancel 事件
    # 这样可以在不依赖ACC的情况下激活/取消系统
    if lateral_only_active and not self.lateral_only_active_last:
      # 仅横向模式刚刚激活，生成 enable 事件
      events.add(EventName.buttonEnable)
      cloudlog.info("[Mode][DEBUG] Lateral-only mode activated: generating buttonEnable event")
    elif not lateral_only_active and self.lateral_only_active_last:
      # 仅横向模式刚刚取消，生成 cancel 事件（如果还没有生成）
      if not any(e == EventName.buttonCancel for e in events.events):
        events.add(EventName.buttonCancel)
        cloudlog.info("[Mode][DEBUG] Lateral-only mode deactivated: generating buttonCancel event")

    self.lateral_only_active_last = lateral_only_active

    # Check for steerTempUnavailable: require ACC active OR TI in RUN state
    # 仅横向模式下不需要ACC激活，跳过此检查
    # 如果启用了LateralOnlyControl但未激活仅横向模式，也跳过此检查（避免状态不一致）
    if not lateral_only_active and not self.lateral_only_control_at_init:
      if not self.CS.acc_active_last and not self.CS.ti_lkas_allowed:
        cloudlog.debug(f"steerTempUnavailable: ACC active={self.CS.acc_active_last}, TI allowed={self.CS.ti_lkas_allowed}")
        events.add(EventName.steerTempUnavailable)

    
    ret.events = events.to_msg()

    self.CS.out = ret.as_reader()
    return self.CS.out

  def apply(self, c):
    ret = self.CC.update(c, self.CS, self.frame)
    self.frame += 1
    return ret
