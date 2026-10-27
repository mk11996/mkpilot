#!/usr/bin/env python3
"""
独立的车辆数据日志记录进程
只在 openpilot 激活控制时记录数据（仅横向模式或完整控制模式）
记录转向角、车速、GPS 等数据到独立的 logs 目录
"""
import os
import time
import logging
from pathlib import Path
from typing import NoReturn
from datetime import datetime

import cereal.messaging as messaging
from selfdrive.swaglog import SwaglogRotatingFileHandler
from selfdrive.hardware import PC
from common.params import Params

# 设置日志目录
if PC:
    VEHICLE_LOG_DIR = os.path.join(str(Path.home()), ".comma", "logs")
else:
    VEHICLE_LOG_DIR = "/data/logs/"

def main() -> NoReturn:
    # 大幅延迟启动：等待系统稳定和openpilot准备就绪
    # 因为不会在启动后立即开启控制，所以可以延迟启动
    print("Vehicle data logger: Delaying startup to reduce boot load...")

    # 延迟30秒启动，让系统先稳定
    time.sleep(30)

    print("Vehicle data logger: Starting initialization...")

    # 创建日志根目录
    Path(VEHICLE_LOG_DIR).mkdir(parents=True, exist_ok=True)

    # 获取当前日期（使用系统时间）
    current_date = datetime.now().strftime('%Y-%m-%d')

    # 检查系统日期是否有效（不是1970-01-01）
    is_system_date_valid = current_date != '1970-01-01'

    if not is_system_date_valid:
        print(f"Vehicle data logger: System date invalid ({current_date}), will use temporary folder and rename when GPS is valid")
    else:
        print(f"Vehicle data logger: System date valid ({current_date})")

    # 确定日期文件夹路径
    # 同一天的所有记录都保存在同一个日期文件夹下
    folder_name = current_date
    date_folder = os.path.join(VEHICLE_LOG_DIR, folder_name)

    # 特殊处理：如果是无效日期（1970-01-01），查找已存在的临时文件夹并递增日期
    if not is_system_date_valid:
        # 查找所有1970年的临时文件夹
        temp_folders = []
        for item in os.listdir(VEHICLE_LOG_DIR):
            if item.startswith('1970-'):
                temp_folders.append(item)

        if temp_folders:
            # 找到最大的日期
            temp_folders.sort()
            last_temp = temp_folders[-1]  # 例如：1970-01-05
            try:
                # 解析日期并加1天
                from datetime import timedelta
                last_date = datetime.strptime(last_temp, '%Y-%m-%d')
                next_date = last_date + timedelta(days=1)

                # 限制在1970年范围内，超过则回到1970-01-01
                if next_date.year > 1970:
                    folder_name = "1970-01-01"
                    print(f"Vehicle data logger: Temp folder year exceeded, resetting to 1970-01-01")
                else:
                    folder_name = next_date.strftime('%Y-%m-%d')
                    print(f"Vehicle data logger: Found existing temp folders, using next date: {folder_name}")
            except:
                # 解析失败，使用默认
                folder_name = current_date
        else:
            # 没有临时文件夹，使用1970-01-01
            folder_name = current_date
            print(f"Vehicle data logger: No existing temp folders, using: {folder_name}")

        date_folder = os.path.join(VEHICLE_LOG_DIR, folder_name)

    # 检查文件夹是否已存在
    folder_exists = os.path.exists(date_folder)

    # 创建日期文件夹（如果不存在）
    Path(date_folder).mkdir(parents=True, exist_ok=True)

    if folder_exists:
        print(f"Vehicle data logger: Using existing date folder: {folder_name}")
    else:
        print(f"Vehicle data logger: Created new date folder: {folder_name}")

    # 在日期文件夹内创建日志文件（使用原来的命名方式，序号自动递增）
    vehicle_log_file = os.path.join(date_folder, "vehicle_data.csv")
    log_handler = SwaglogRotatingFileHandler(
        vehicle_log_file,
        interval=999999,          # 设置极大值，实际上禁用时间轮换
        max_bytes=999999999999,   # 设置极大值，实际上禁用大小轮换
        backup_count=500          # 保留 500 个文件
    )

    # 设置 CSV 格式
    log_handler.setFormatter(logging.Formatter('%(message)s'))

    logger = logging.getLogger('vehicle_data')
    logger.setLevel(logging.INFO)
    logger.addHandler(log_handler)

    # 立即写入CSV标题（新文件已在doRollover中创建）
    logger.info(
        "日期时间,"
        "消息时间戳(秒),"
        "系统时间戳(秒),"
        "GPS时间,"
        "实际转向角(度),"
        "期望转向角(度),"
        "车速(km/h),"
        "油门(%),"
        "刹车(%),"
        "驾驶员扭矩(Nm),"
        "驾驶员干预,"
        "EPS电机扭矩(Nm),"
        "GPS纬度,"
        "GPS经度,"
        "GPS海拔(米),"
        "档位,"
        "巡航启用,"
        "OP启用,"
        "仅横向模式"
    )

    print(f"Vehicle data logger: Log file created")

    # 订阅消息
    sm = messaging.SubMaster([
        'carState',           # 实际转向角、车速、油门、刹车
        'carControl',         # 预期转向角
        'controlsState',      # 控制状态
        'liveLocationKalman', # GPS 位置数据
        'gpsLocationExternal' # GPS 原始数据（包含时间戳）
    ])

    # 读取参数
    params = Params()

    print("Vehicle data logger: Waiting for messages to be available...")

    # 缩短智能等待超时：从30秒改为15秒
    startup_timeout = 15
    startup_start = time.time()
    messages_ready = False

    while not messages_ready and (time.time() - startup_start) < startup_timeout:
        sm.update(timeout=1000)

        # 检查关键消息是否可用
        if sm.valid['carState'] and sm.valid['controlsState']:
            messages_ready = True
            print("Vehicle data logger: Messages available, starting logger")
        else:
            # 降低状态打印频率：从每2秒改为每5秒
            elapsed = int(time.time() - startup_start)
            if elapsed > 0 and elapsed % 5 == 0:
                print(f"Vehicle data logger: Waiting for messages... "
                      f"(carState: {sm.valid['carState']}, "
                      f"controlsState: {sm.valid['controlsState']})")
            time.sleep(1)  # 增加sleep，降低CPU占用

    if not messages_ready:
        print("Vehicle data logger: WARNING - Timeout waiting for messages, starting anyway")

    print("Vehicle data logger started")
    print(f"Logging to: {VEHICLE_LOG_DIR}")
    print("Waiting for openpilot to be enabled...")

    frame_counter = 0
    recording_active = False
    param_read_counter = 0
    lateral_only_active = False

    # 文件夹重命名相关变量
    current_folder_path = date_folder  # 当前使用的文件夹路径
    folder_renamed = False  # 是否已经重命名过
    is_temp_folder = folder_name.startswith('1970-')  # 是否是临时文件夹（以1970-开头）
    folder_creation_time = time.time()  # 记录文件夹创建时间（用于判断是否是当天创建）

    # GPS 时间有效性阈值：2026-01-01 00:00:00 的毫秒时间戳
    GPS_TIME_THRESHOLD_MS = 1767225600000  # 2026年之前的时间视为无效

    while True:
        try:
            sm.update(timeout=1000)  # 1 秒超时

            # 检查数据有效性
            if not sm.valid['carState']:
                continue

            # 【文件夹重命名逻辑】只修正当天创建的临时文件夹
            # 判断条件：
            # 1. 是临时文件夹（1970-开头）
            # 2. 尚未重命名
            # 3. 文件夹是最近创建的（启动后创建，而不是历史遗留）
            if is_temp_folder and not folder_renamed:
                try:
                    if sm.valid['gpsLocationExternal']:
                        GPS_EXT = sm['gpsLocationExternal']
                        gps_timestamp_ms = GPS_EXT.timestamp

                        # 检查GPS时间是否有效（大于2026年阈值）
                        if gps_timestamp_ms and gps_timestamp_ms >= GPS_TIME_THRESHOLD_MS:
                            # 从GPS时间获取日期
                            gps_timestamp_sec = gps_timestamp_ms / 1000.0
                            from datetime import timezone, timedelta
                            utc_time = datetime.fromtimestamp(gps_timestamp_sec, tz=timezone.utc)
                            china_time = utc_time + timedelta(hours=8)
                            gps_date = china_time.strftime('%Y-%m-%d')

                            # 检查文件夹是否是本次启动创建的（而不是历史遗留）
                            # 通过检查文件夹的修改时间来判断
                            folder_mtime = os.path.getmtime(current_folder_path)
                            time_since_creation = time.time() - folder_mtime

                            # 如果文件夹是最近1小时内创建/修改的，认为是本次启动创建的
                            is_current_session = time_since_creation < 3600  # 1小时

                            if not is_current_session:
                                print(f"Vehicle data logger: Skipping rename of old temp folder {folder_name} (created {time_since_creation/3600:.1f}h ago)")
                                folder_renamed = True  # 标记为已处理，不再尝试
                                continue

                            # 如果GPS日期与临时日期不同，重命名文件夹
                            if gps_date != folder_name and gps_date.startswith('20'):  # 确保是有效的20xx年日期
                                # 构建新文件夹名（只使用日期，不带序号）
                                new_folder_name = gps_date
                                new_folder_path = os.path.join(VEHICLE_LOG_DIR, new_folder_name)

                                # 检查新文件夹名是否已存在
                                if not os.path.exists(new_folder_path):
                                    # 重命名文件夹
                                    os.rename(current_folder_path, new_folder_path)
                                    current_folder_path = new_folder_path
                                    folder_renamed = True
                                    is_temp_folder = False
                                    print(f"Vehicle data logger: Folder renamed from {folder_name} to {new_folder_name} (GPS time valid)")

                                    # 重新创建日志句柄，指向新路径
                                    logger.handlers.clear()
                                    vehicle_log_file = os.path.join(current_folder_path, "vehicle_data.csv")
                                    log_handler = SwaglogRotatingFileHandler(
                                        vehicle_log_file,
                                        interval=999999,
                                        max_bytes=999999999999,
                                        backup_count=500
                                    )
                                    log_handler.setFormatter(logging.Formatter('%(message)s'))
                                    logger.addHandler(log_handler)
                                    print(f"Vehicle data logger: Logger updated to new path: {vehicle_log_file}")
                                else:
                                    # 新文件夹已存在，需要合并数据
                                    # 将当前日志文件移动到已存在的文件夹中
                                    import shutil

                                    # 先关闭当前日志句柄
                                    logger.handlers.clear()

                                    # 扫描目标文件夹，找到最大的文件序号
                                    max_idx = -1
                                    for existing_file in os.listdir(new_folder_path):
                                        if existing_file.startswith('vehicle_data.') and existing_file.endswith('.csv'):
                                            # 提取序号
                                            parts = existing_file.split('.')
                                            if len(parts) >= 3 and parts[-2].isdigit():
                                                max_idx = max(max_idx, int(parts[-2]))
                                            elif len(parts) >= 3 and '_' in parts[-2]:
                                                idx_part = parts[-2].split('_')[0]
                                                if idx_part.isdigit():
                                                    max_idx = max(max_idx, int(idx_part))

                                    # 移动文件，使用下一个序号
                                    for file in os.listdir(current_folder_path):
                                        src = os.path.join(current_folder_path, file)

                                        # 为临时文件分配新的序号
                                        max_idx += 1
                                        base, ext = os.path.splitext(file)
                                        # 生成标准格式的文件名：vehicle_data.序号.csv
                                        new_filename = f"vehicle_data.{max_idx:010}.csv"
                                        dst = os.path.join(new_folder_path, new_filename)

                                        shutil.move(src, dst)
                                        print(f"Vehicle data logger: Moved {file} to {new_filename}")

                                    # 删除空的临时文件夹
                                    os.rmdir(current_folder_path)
                                    current_folder_path = new_folder_path
                                    folder_renamed = True
                                    is_temp_folder = False
                                    print(f"Vehicle data logger: Merged {folder_name} into existing {new_folder_name}")

                                    # 重新创建日志句柄，指向合并后的路径
                              # 关键：让handler扫描已存在文件，自动找到最新的序号
                                    vehicle_log_file = os.path.join(current_folder_path, "vehicle_data.csv")
                                    log_handler = SwaglogRotatingFileHandler(
                                        vehicle_log_file,
                                        interval=999999,
                                        max_bytes=999999999999,
                                        backup_count=500
                                    )
                                    log_handler.setFormatter(logging.Formatter('%(message)s'))
                                    logger.addHandler(log_handler)

                                    # 写入CSV标题到新文件（handler已经创建了新文件）
                                    logger.info(
                                        "日期时间,"
                                        "消息时间戳(秒),"
                                        "系统时间戳(秒),"
                                        "GPS时间,"
                                        "实际转向角(度),"
                                        "期望转向角(度),"
                                        "车速(km/h),"
                                        "油门(%),"
                                        "刹车(%),"
                                        "驾驶员扭矩(Nm),"
                                        "驾驶员干预,"
                                        "EPS电机扭矩(Nm),"
                                        "GPS纬度,"
                                        "GPS经度,"
                                        "GPS海拔(米),"
                                        "档位,"
                                        "巡航启用,"
                              "OP启用,"
                                        "仅横向模式"
                                    )

                                    print(f"Vehicle data logger: Logger updated after merge: {vehicle_log_file}")
                except Exception as e:
                    print(f"Vehicle data logger: Folder rename error: {e}")

            # 每 30 帧读取一次参数（约 0.3 秒），避免频繁读磁盘
            param_read_counter += 1
            if param_read_counter >= 30:
                param_read_counter = 0
                lateral_only_active = params.get_bool("LateralOnlyActive")

            # 获取控制状态
            CTRL = sm['controlsState']
            openpilot_enabled = CTRL.enabled if sm.valid['controlsState'] else False

            # 检查GPS数据是否有效（作为记录触发条件）
            GPS = sm['liveLocationKalman']
            gps_valid = False
            try:
                if sm.valid['liveLocationKalman'] and hasattr(GPS, 'positionGeodetic') and GPS.positionGeodetic.valid:
                    gps_lat = GPS.positionGeodetic.value[0]
                    gps_lon = GPS.positionGeodetic.value[1]
                    # 只要有有效的经纬度数据就记录（不限制地理位置）
                    # 简单检查坐标是否在合理范围内（全球范围）
                    if (-90 <= gps_lat <= 90 and -180 <= gps_lon <= 180):
                        gps_valid = True
            except (AttributeError, IndexError):
                pass

            # 新的记录触发条件：GPS有效即开始记录
            should_record = gps_valid

            # 动态记录频率：
            # - 控制模式激活时：10帧一次（约0.1秒，10 Hz）- 详细记录
            # - 正常情况：30帧一次（约0.3秒，约3.3 Hz）- 轨迹记录
            control_active = openpilot_enabled or lateral_only_active
            record_interval = 10 if control_active else 30

            # 记录状态变化
            if should_record and not recording_active:
                print("Vehicle data logger: Recording started (GPS valid)")
                recording_active = True
                frame_counter = 0  # 重置计数器，立即开始记录

            elif not should_record and recording_active:
                print("Vehicle data logger: Recording paused (GPS invalid)")
                recording_active = False

            # 如果不应该记录，跳过
            if not should_record:
                frame_counter = 0
                continue

            # 动态调整记录频率
            frame_counter += 1
            if frame_counter < record_interval:
                continue
            frame_counter = 0

            # 获取数据
            CS = sm['carState']
            CC = sm['carControl']

            # 提取时间戳（使用消息自带的时间戳，纳秒转秒）
            # 检查 logMonoTime 是否存在
            if 'carState' in sm.logMonoTime:
                msg_timestamp = sm.logMonoTime['carState'] / 1e9
            else:
                msg_timestamp = time.time()
            system_timestamp = time.time()

            # 提取转向角度
            actual_steering = CS.steeringAngleDeg

            # 检查 carControl 是否有效
            if sm.valid['carControl']:
                desired_steering = CC.actuators.steeringAngleDeg
            else:
                desired_steering = 0.0  # 默认值

            # 提取速度（m/s 转 km/h）
            speed_kmh = CS.vEgo * 3.6

            # 提取油门和刹车（已归一化为0-1）
            # 油门：0-1范围，转换为百分比
            throttle_percent = CS.gas * 100.0
            # 刹车：0-1范围，转换为百分比
            brake_percent = CS.brake * 100.0

            # 提取驾驶员干预数据（带异常保护）
            try:
                steering_torque = CS.steeringTorque  # 驾驶员扭矩 (Nm)
                steering_pressed = CS.steeringPressed  # 是否正在干预 (布尔值)
                steering_torque_eps = CS.steeringTorqueEps  # EPS电机扭矩 (Nm)
            except (AttributeError, KeyError):
                # 如果字段不存在（例如没有TI或消息不可用），使用默认值
                steering_torque = 0.0
                steering_pressed = False
                steering_torque_eps = 0.0

            # GPS 数据（如果有效）
            try:
                if sm.valid['liveLocationKalman'] and hasattr(GPS, 'positionGeodetic') and GPS.positionGeodetic.valid:
                    gps_lat = GPS.positionGeodetic.value[0]
                    gps_lon = GPS.positionGeodetic.value[1]
                    gps_alt = GPS.positionGeodetic.value[2]
                else:
                    gps_lat = gps_lon = gps_alt = None
            except (AttributeError, IndexError):
                gps_lat = gps_lon = gps_alt = None

            # GPS 时间戳（从 gpsLocationExternal 获取）
            gps_time_str = "N/A"  # 默认值
            try:
                if sm.valid['gpsLocationExternal']:
                    GPS_EXT = sm['gpsLocationExternal']
                    gps_timestamp_ms = GPS_EXT.timestamp  # 毫秒时间戳

                    # 验证 GPS 时间戳有效性（过滤 2026 年之前的时间）
                    if gps_timestamp_ms and gps_timestamp_ms >= GPS_TIME_THRESHOLD_MS:
                        # 转换为秒级时间戳
                        gps_timestamp_sec = gps_timestamp_ms / 1000.0
                        # GPS时间戳是UTC时间，手动加8小时转换为中国时间（UTC+8）
                        from datetime import timezone, timedelta
                        utc_time = datetime.fromtimestamp(gps_timestamp_sec, tz=timezone.utc)
                        china_time = utc_time + timedelta(hours=8)  # 手动加8小时
                        gps_time_str = china_time.strftime('%Y-%m-%d %H:%M:%S')
                    # 如果时间戳无效（小于阈值），保持默认值 "N/A"
            except (AttributeError, KeyError, ValueError, OSError) as e:
                # 时间戳转换可能失败（例如超出范围），保持默认值
                pass

            # 其他数据
            try:
                gear = str(CS.gearShifter)
            except:
                gear = "unknown"

            try:
                cruise_enabled = CS.cruiseState.enabled
            except (AttributeError, KeyError):
                cruise_enabled = False

            # 生成可读的日期时间字符串
            readable_time = datetime.fromtimestamp(system_timestamp).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

            # 记录到日志（CSV 格式）
            logger.info(
                f"{readable_time},"
                f"{msg_timestamp:.6f},"
                f"{system_timestamp:.6f},"
                f"{gps_time_str},"
                f"{actual_steering:.2f},"
                f"{desired_steering:.2f},"
                f"{speed_kmh:.2f},"
                f"{throttle_percent:.2f},"
                f"{brake_percent:.2f},"
                f"{steering_torque:.2f},"
                f"{steering_pressed},"
                f"{steering_torque_eps:.2f},"
                f"{gps_lat},"
                f"{gps_lon},"
                f"{gps_alt},"
                f"{gear},"
                f"{cruise_enabled},"
                f"{openpilot_enabled},"
                f"{lateral_only_active}"
            )

        except KeyboardInterrupt:
            print("Vehicle data logger: Shutting down...")
            break
        except Exception as e:
            # 记录错误但不崩溃
            print(f"Vehicle data logger: Error - {e}")
            time.sleep(1)  # 避免错误循环过快

if __name__ == "__main__":
    main()
