#!/usr/bin/env python3
"""
本地行程统计模块
实时统计驾驶距离、时间、行程数，即使离线也能查看
"""
import time
import json
from typing import NoReturn
from datetime import datetime, timedelta

from common.params import Params
from cereal.messaging import SubMaster
from selfdrive.swaglog import cloudlog

# 统计数据键名
STATS_ALL_TIME = "StatsAllTime"
STATS_WEEK = "StatsWeek"
STATS_LAST_RESET = "StatsLastReset"

# 合理性检查常量
MAX_DT = 5.0  # 最大允许时间差（秒），防止时间跳变
MAX_SPEED = 70.0  # 最大允许速度（m/s），约250 km/h
MIN_VALID_TIMESTAMP = 1577836800  # 2020-01-01的时间戳，用于检测时间是否有效

class LocalStats:
  def __init__(self):
    self.params = Params()
    # 添加超时参数，确保能获取到数据
    self.sm = SubMaster(['carState', 'deviceState'], poll=['carState'])

    # 加载已有统计数据
    self.stats_all = self.load_stats(STATS_ALL_TIME)
    self.stats_week = self.load_stats(STATS_WEEK)

    cloudlog.info(f"Loaded stats - All: {self.stats_all}, Week: {self.stats_week}")

    # 初始化时立即保存一次，确保参数文件存在（即使数据为0）
    self.save_stats(STATS_ALL_TIME, self.stats_all)
    self.save_stats(STATS_WEEK, self.stats_week)
    cloudlog.info("Initial stats saved to ensure params exist")

    # 当前行程状态
    self.current_route_active = False
    self.current_route_distance = 0.0  # 米
    self.current_route_start_time = 0.0
    self.current_route_duration = 0.0  # 秒

    # 上次更新状态（使用单调时钟，不受系统时间影响）
    self.last_v_ego = 0.0
    self.last_update_monotonic = time.monotonic()
    self.last_started = False
    self.last_save_monotonic = time.monotonic()  # 上次保存时间（用于定期保存）
    self.last_gear = None  # 上次档位状态，用于检测P档切换

    # 初始化时检查周统计是否需要重置
    self.check_week_reset()

  def load_stats(self, key: str) -> dict:
    """加载统计数据"""
    try:
      data = self.params.get(key)  # 返回 bytes 或 None
      if data:
        return json.loads(data.decode('utf-8'))  # 解码后再 JSON 解析
    except Exception as e:
      cloudlog.warning(f"Failed to load {key}: {e}")

    # 返回默认值
    return {
      "routes": 0,        # 行程数
      "distance": 0.0,    # 总距离（米）
      "minutes": 0.0,     # 总时长（分钟）
    }

  def save_stats(self, key: str, stats: dict):
    """保存统计数据"""
    try:
      # JSON 序列化后编码成字节
      self.params.put(key, json.dumps(stats).encode('utf-8'))
    except Exception as e:
      cloudlog.warning(f"Failed to save {key}: {e}")

  def check_week_reset(self):
    """检查是否需要重置周统计（防止时间跳变影响）"""
    try:
      current_timestamp = time.time()

      # 检查当前时间是否有效（避免1970年等异常时间）
      if current_timestamp < MIN_VALID_TIMESTAMP:
        cloudlog.warning(f"Invalid system time detected: {datetime.fromtimestamp(current_timestamp)}, skipping week reset check")
        return

      last_reset_data = self.params.get(STATS_LAST_RESET)  # 返回 bytes
      if last_reset_data:
        last_reset_str = last_reset_data.decode('utf-8')
        last_reset = datetime.fromisoformat(last_reset_str)
        last_reset_timestamp = last_reset.timestamp()

        # 检查上次重置时间是否有效
        if last_reset_timestamp < MIN_VALID_TIMESTAMP:
          cloudlog.warning(f"Invalid last reset time: {last_reset}, resetting to current time")
          self.params.put(STATS_LAST_RESET, datetime.now().isoformat().encode('utf-8'))
          return

        # 检查时间是否向后跳变（系统时间被重置）
        if current_timestamp < last_reset_timestamp:
          cloudlog.warning(f"Time jumped backwards detected, resetting last reset time")
          self.params.put(STATS_LAST_RESET, datetime.now().isoformat().encode('utf-8'))
          return

        now = datetime.now()
        days_diff = (now - last_reset).days

        # 如果超过7天，重置周统计
        if days_diff >= 7:
          cloudlog.info(f"Resetting week stats after {days_diff} days")
          self.stats_week = {"routes": 0, "distance": 0.0, "minutes": 0.0}
          self.params.put(STATS_LAST_RESET, now.isoformat().encode('utf-8'))
          self.save_stats(STATS_WEEK, self.stats_week)
          cloudlog.info("Week stats reset")
      else:
        # 首次运行，设置重置时间
        self.params.put(STATS_LAST_RESET, datetime.now().isoformat().encode('utf-8'))
        cloudlog.info("Initialized week reset timestamp")
    except Exception as e:
      cloudlog.warning(f"Failed to check week reset: {e}")

  def update(self):
    """更新统计数据"""
    self.sm.update(0)  # 使用非阻塞更新

    # 使用单调时钟计算时间差（不受系统时间跳变影响）
    current_monotonic = time.monotonic()
    dt = current_monotonic - self.last_update_monotonic

    # 防止异常时间差（长时间暂停、系统休眠等）
    # 正常更新频率是20Hz（0.05秒），允许最大5秒的时间差
    if dt > MAX_DT or dt < 0:
      cloudlog.warning(f"Abnormal dt detected: {dt:.2f}s (possible system suspend), resetting timer")
      self.last_update_monotonic = current_monotonic
      self.last_v_ego = 0.0  # 重置速度避免下次计算异常
      return

    # 检查数据是否有效
    if not self.sm.valid['deviceState'] or not self.sm.valid['carState']:
      # 每10秒打印一次等待消息，避免日志过多
      if int(current_monotonic) % 10 == 0:
        cloudlog.debug(f"Waiting for valid data - deviceState: {self.sm.valid['deviceState']}, "
                      f"carState: {self.sm.valid['carState']}")
      self.last_update_monotonic = current_monotonic
      return

    # 获取当前状态
    started = self.sm['deviceState'].started
    v_ego = self.sm['carState'].vEgo  # m/s
    current_gear = self.sm['carState'].gearShifter  # 当前档位

    # 验证速度合理性（防止异常数据）
    # 容忍微小的负值（Kalman滤波器在静止时可能产生-0.05 m/s以内的负值）
    if v_ego < -0.1 or v_ego > MAX_SPEED:
      cloudlog.warning(f"Abnormal speed detected: {v_ego:.2f} m/s, skipping update")
      self.last_update_monotonic = current_monotonic
      self.last_v_ego = 0.0  # 重置速度
      return

    # 将微小负值修正为0（车辆不可能倒退）
    if v_ego < 0:
      v_ego = 0.0

    # 检测P档切换（挂入P档时自动保存）
    # 只有从非P档切换到P档，且有累积数据时才保存
    if self.last_gear is not None and self.last_gear != 'park' and current_gear == 'park':
      if self.current_route_active and self.current_route_distance > 0:
        cloudlog.info(f"Gear shifted to Park, saving progress ({self.current_route_distance:.1f}m)")
        self.save_current_progress()
        self.last_save_monotonic = current_monotonic  # 更新保存时间，避免立即再次定时保存

    # 检测行程开始
    if started and not self.last_started:
      self.on_route_start()

    # 检测行程结束
    elif not started and self.last_started:
      self.on_route_end()

    # 行程进行中，累计数据
    if started and self.current_route_active:
      # 累计距离（梯形积分法，更准确）
      distance_increment = ((v_ego + self.last_v_ego) / 2.0) * dt
      self.current_route_distance += distance_increment

      # 累计时长
      self.current_route_duration += dt

      # 每30秒输出一次当前行程状态，方便调试
      if int(current_monotonic) % 30 == 0 and int(self.current_route_duration) > 0:
        cloudlog.debug(f"Current route - Distance: {self.current_route_distance:.1f}m, "
                      f"Duration: {self.current_route_duration:.1f}s, "
                      f"Speed: {v_ego:.1f}m/s")

      # 定期保存机制：每120秒保存一次当前累积的数据（防止数据丢失）
      # 由于增加了P档自动保存，定时保存间隔可以延长
      if current_monotonic - self.last_save_monotonic >= 120.0:
        self.save_current_progress()
        self.last_save_monotonic = current_monotonic

    # 更新状态
    self.last_v_ego = v_ego
    self.last_update_monotonic = current_monotonic
    self.last_started = started
    self.last_gear = current_gear  # 更新档位状态

  def save_current_progress(self):
    """定期保存当前行程的累积数据（行程还在进行中）"""
    if not self.current_route_active:
      return

    # 只有累积了有效数据才保存（距离>10米或时长>5秒）
    if self.current_route_distance > 10.0 or self.current_route_duration > 5.0:
      # 把当前行程的累积数据同步到总统计中
      self.stats_all["distance"] += self.current_route_distance
      self.stats_all["minutes"] += self.current_route_duration / 60.0

      self.stats_week["distance"] += self.current_route_distance
      self.stats_week["minutes"] += self.current_route_duration / 60.0

      # 保存到持久化存储
      self.save_stats(STATS_ALL_TIME, self.stats_all)
      self.save_stats(STATS_WEEK, self.stats_week)

      cloudlog.info(f"Progress saved: {self.current_route_distance:.1f}m, "
                   f"{self.current_route_duration:.1f}s added to total")

      # 重置当前行程的累积计数器（但保持 active 状态，继续累积新的增量）
      self.current_route_distance = 0.0
      self.current_route_duration = 0.0

  def on_route_start(self):
    """行程开始"""
    self.current_route_active = True
    self.current_route_distance = 0.0
    self.current_route_start_time = time.monotonic()
    self.current_route_duration = 0.0

    # 每次行程开始时检查周统计是否需要重置
    self.check_week_reset()

    cloudlog.info("Route started")

  def on_route_end(self):
    """行程结束，保存数据"""
    if not self.current_route_active:
      return

    # 行程结束，保存最后一段还没保存的增量数据
    # 注意：大部分数据可能已经通过定期保存机制保存了，这里只保存最后的增量

    # 累加最后的增量（如果有的话）
    if self.current_route_distance > 0 or self.current_route_duration > 0:
      self.stats_all["distance"] += self.current_route_distance
      self.stats_all["minutes"] += self.current_route_duration / 60.0

      self.stats_week["distance"] += self.current_route_distance
      self.stats_week["minutes"] += self.current_route_duration / 60.0

    # 增加行程计数（无论距离多少，只要有行程就计数）
    self.stats_all["routes"] += 1
    self.stats_week["routes"] += 1

    # 保存最终数据到持久化存储
    self.save_stats(STATS_ALL_TIME, self.stats_all)
    self.save_stats(STATS_WEEK, self.stats_week)

    cloudlog.info(f"Route ended and saved - Last increment: {self.current_route_distance:.1f}m, "
                 f"{self.current_route_duration:.1f}s | "
                 f"Total routes: {self.stats_all['routes']}, "
                 f"Total distance: {self.stats_all['distance']:.1f}m")

    # 重置当前行程状态
    self.current_route_active = False
    self.current_route_distance = 0.0
    self.current_route_duration = 0.0


def main() -> NoReturn:
  """主循环"""
  cloudlog.info("Local stats daemon started")

  stats = LocalStats()

  while True:
    try:
      stats.update()

      # 5Hz 更新频率（降低CPU占用，统计精度仍然足够）
      time.sleep(0.2)

    except Exception as e:
      cloudlog.exception(f"Local stats error: {e}")
      time.sleep(1.0)


if __name__ == "__main__":
  main()
