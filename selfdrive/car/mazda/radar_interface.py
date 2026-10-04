#!/usr/bin/env python3
import math
from cereal import car
from opendbc.can.parser import CANParser
from selfdrive.car.interfaces import RadarInterfaceBase
from selfdrive.swaglog import cloudlog


def _create_radar_can_parser(car_fingerprint):
  """
  马自达雷达系统：6个独立的雷达追踪消息 (ID 865-870)
  每个消息代表一个被追踪的目标，结构完全相同

  使用 mazda_2017.dbc（已修改为包含正确的雷达信号定义）
  """
  # 6个雷达追踪消息的ID
  RADAR_MSGS = [865, 866, 867, 868, 869, 870]

  signals = []
  checks = []

  for msg_id in RADAR_MSGS:
    # 每个消息包含3个信号（根据修改后的 mazda_2017.dbc）
    signals += [
      ('DIST_OBJ', msg_id),   # 距离：12位无符号，÷16 = 米
      ('ANG_OBJ', msg_id),    # 角度：12位有符号，÷64 = 度
      ('RELV_OBJ', msg_id),   # 相对速度：11位有符号，÷64 = m/s
    ]

    # 检查频率：20Hz（根据马自达雷达规格）
    checks.append((msg_id, 20))

  # 使用 mazda_2017.dbc（已包含正确的雷达信号定义）
  return CANParser('mazda_2017', signals, checks, 0)


class RadarInterface(RadarInterfaceBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.radar_fault = False
    self.radar_ts = CP.radarTimeStep

    # 创建雷达 CAN 解析器
    self.rcp = _create_radar_can_parser(CP.carFingerprint)

    # 6个雷达追踪消息的ID
    self.RADAR_MSGS = [865, 866, 867, 868, 869, 870]

    # 触发消息：使用第一个雷达消息作为触发
    self.trigger_msg = 865
    self.updated_messages = set()

    # 转换系数（根据 mazda_radar.dbc 和实际测试）
    self.DIST_SCALE = 1.0 / 16.0   # DIST_OBJ ÷ 16 = 米
    self.ANG_SCALE = 1.0 / 64.0    # ANG_OBJ ÷ 64 = 度
    self.RELV_SCALE = 1.0 / 64.0   # RELV_OBJ ÷ 64 = m/s

    # 调试计数器（每50帧输出一次）
    self.debug_counter = 0

    cloudlog.warning("[RADAR] Mazda radar interface initialized - 6 target tracking system")

  def update(self, can_strings):
    vls = self.rcp.update_strings(can_strings)
    self.updated_messages.update(vls)

    if self.trigger_msg not in self.updated_messages:
      return None

    rr = self._update(self.updated_messages)
    self.updated_messages.clear()
    return rr

  def _update(self, updated_messages):
    ret = car.RadarData.new_message()

    # 遍历所有6个雷达消息，解析每个目标
    valid_targets = 0
    debug_info = []  # 收集调试信息

    for msg_id in self.RADAR_MSGS:
      if msg_id in updated_messages:
        cpt = self.rcp.vl[msg_id]

        # 读取原始值
        dist_raw = cpt['DIST_OBJ']
        ang_raw = cpt['ANG_OBJ']
        relv_raw = cpt['RELV_OBJ']

        # 转换为物理值
        dist_m = dist_raw * self.DIST_SCALE        # 米
        ang_deg = ang_raw * self.ANG_SCALE         # 度
        ang_rad = math.radians(ang_deg)            # 弧度
        relv_ms = relv_raw * self.RELV_SCALE       # m/s

        # 收集调试信息（每50帧输出一次）
        if self.debug_counter == 0:
          debug_info.append(f"Msg{msg_id}: raw({dist_raw},{ang_raw},{relv_raw}) -> ({dist_m:.1f}m,{ang_deg:.1f}°,{relv_ms:.1f}m/s)")

        # 检查目标是否有效
        # 根据Mazda雷达协议：
        # - DIST_OBJ=4095: 距离无效
        # - ANG_OBJ=2046: 角度无效
        # - RELV_OBJ=-16: 速度无效（参考代码中的标记）
        # - RELV_OBJ=±1023: 速度饱和/无效（11位有符号整数的边界值）
        if dist_raw != 4095 and ang_raw != 2046 and relv_raw != -16 and abs(relv_raw) != 1023:
          # 计算横向距离（yRel）
          # yRel = dRel * sin(angle)
          # 但由于角度很小，可以近似为：yRel ≈ dRel * angle_rad
          y_rel = dist_m * math.sin(ang_rad)

          # 创建或更新雷达点
          if msg_id not in self.pts:
            self.pts[msg_id] = car.RadarData.RadarPoint.new_message()
            self.pts[msg_id].trackId = msg_id  # 使用消息ID作为trackId

          # 设置雷达点数据
          self.pts[msg_id].dRel = dist_m      # 纵向距离（米）
          self.pts[msg_id].yRel = y_rel       # 横向距离（米）
          self.pts[msg_id].vRel = relv_ms     # 相对速度（m/s）
          self.pts[msg_id].aRel = float('nan')  # 加速度（不可用）
          self.pts[msg_id].yvRel = float('nan') # 横向速度（不可用）
          self.pts[msg_id].measured = True

          valid_targets += 1
        else:
          # 距离无效，删除雷达点
          if msg_id in self.pts:
            del self.pts[msg_id]

    # 每50帧输出一次调试信息
    if self.debug_counter == 0 and len(debug_info) > 0:
      cloudlog.warning(f"[RADAR] Valid targets: {valid_targets}/{len(debug_info)}")
      for info in debug_info:
        cloudlog.warning(f"[RADAR]   {info}")

    self.debug_counter = (self.debug_counter + 1) % 50

    # 检查错误
    errors = []
    if not self.rcp.can_valid:
      errors.append("canError")
      cloudlog.warning("[RADAR] CAN parser not valid!")
    if self.radar_fault:
      errors.append("fault")
    ret.errors = errors

    # 返回所有雷达点
    ret.points = list(self.pts.values())

    return ret
