#!/usr/bin/env python3
"""
GPS时间同步服务
在系统启动后，等待GPS获取有效时间，然后设置系统时间
同时重命名启动时创建的临时日志目录
"""
import os
import time
import datetime
import subprocess
from pathlib import Path

import cereal.messaging as messaging
from selfdrive.swaglog import cloudlog
from common.time import MIN_DATE

# GPS时间有效性阈值：2026-01-01 00:00:00 的毫秒时间戳
GPS_TIME_THRESHOLD_MS = 1767225600000

def set_system_time_from_gps(gps_timestamp_ms):
    """从GPS时间戳设置系统时间"""
    try:
        # 转换为秒级时间戳
        gps_timestamp_sec = gps_timestamp_ms / 1000.0
        gps_time = datetime.datetime.fromtimestamp(gps_timestamp_sec, tz=datetime.timezone.utc)

        cloudlog.info(f"Setting system time from GPS: {gps_time}")

        # 设置系统时间（需要root权限）
        # 使用多种方法尝试设置时间

        # 方法1：使用date命令（Linux标准）
        time_str = gps_time.strftime('%Y-%m-%d %H:%M:%S')
        result = subprocess.run(
            ['date', '-u', '-s', time_str],  # -u 表示UTC时间
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            cloudlog.info("System time set successfully from GPS using date command")
            return True

        # 方法2：如果date命令失败，尝试使用timedatectl（systemd系统）
        cloudlog.warning(f"date command failed: {result.stderr}, trying timedatectl")
        time_str_iso = gps_time.strftime('%Y-%m-%d %H:%M:%S')
        result = subprocess.run(
            ['timedatectl', 'set-time', time_str_iso],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            cloudlog.info("System time set successfully from GPS using timedatectl")
            return True

        cloudlog.error(f"Failed to set system time: {result.stderr}")
        return False

    except Exception as e:
        cloudlog.error(f"Error setting system time from GPS: {e}")
        return False

def rename_pending_logs(log_root, new_route_name, existing_pending_dirs):
    """重命名pending开头的日志目录（仅重命名本次启动后新创建的）

    Args:
        log_root: 日志根目录
        new_route_name: 新的route名称
        existing_pending_dirs: 服务启动时已存在的pending文件夹名称集合
    """
    try:
        log_path = Path(log_root)
        if not log_path.exists():
            return 0

        # 查找所有pending开头的目录
        all_pending_dirs = [d for d in log_path.iterdir()
                           if d.is_dir() and d.name.startswith('pending--')]

        # 只保留本次启动后新创建的pending文件夹（不在启动时的列表中）
        pending_dirs = []
        for d in all_pending_dirs:
            if d.name not in existing_pending_dirs:
                pending_dirs.append(d)
                cloudlog.info(f"Found new pending directory: {d.name}")
            else:
                cloudlog.info(f"Skipping existing pending directory: {d.name} (existed before service start)")

        pending_dirs = sorted(pending_dirs)

        if not pending_dirs:
            cloudlog.info("No pending log directories to rename")
            return 0

        cloudlog.info(f"Found {len(pending_dirs)} pending log directories")

        # 查找已存在的同名route的最大段号，避免冲突
        existing_segments = [d for d in log_path.iterdir()
                           if d.is_dir() and d.name.startswith(new_route_name + '--')]

        max_segment = -1
        for seg_dir in existing_segments:
            try:
                # 提取段号：route_name--N
                seg_num = int(seg_dir.name.split('--')[-1])
                max_segment = max(max_segment, seg_num)
            except (ValueError, IndexError):
                pass

        # 从max_segment+1开始编号
        start_segment = max_segment + 1
        cloudlog.info(f"Starting segment numbering from {start_segment}")

        # 重命名每个目录
        renamed_count = 0
        for i, old_dir in enumerate(pending_dirs):
            # 生成新的目录名：route_name--segment
            new_segment_num = start_segment + i
            new_dir_name = f"{new_route_name}--{new_segment_num}"
            new_dir_path = log_path / new_dir_name

            try:
                old_dir.rename(new_dir_path)
                cloudlog.info(f"Renamed: {old_dir.name} -> {new_dir_name}")
                renamed_count += 1
            except Exception as e:
                cloudlog.error(f"Failed to rename {old_dir.name}: {e}")

        cloudlog.info(f"Successfully renamed {renamed_count}/{len(pending_dirs)} directories")
        return renamed_count

    except Exception as e:
        cloudlog.error(f"Error renaming pending logs: {e}")
        return 0

def main():
    """主函数：监控GPS时间并同步系统时间"""
    cloudlog.info("GPS time sync service started")

    # 记录服务启动时已存在的pending文件夹（用于过滤）
    from selfdrive.loggerd.config import ROOT as LOG_ROOT
    existing_pending_dirs = set()
    try:
        log_path = Path(LOG_ROOT)
        if log_path.exists():
            existing_pending_dirs = {d.name for d in log_path.iterdir()
                                    if d.is_dir() and d.name.startswith('pending--')}
            if existing_pending_dirs:
                cloudlog.info(f"Found {len(existing_pending_dirs)} existing pending directories at service start")
    except Exception as e:
        cloudlog.warning(f"Failed to list existing pending directories: {e}")

    # 检查系统时间是否已经有效
    sys_time = datetime.datetime.now()
    if sys_time > MIN_DATE:
        cloudlog.info(f"System time already valid: {sys_time}")
        return

    cloudlog.info(f"System time invalid: {sys_time}, waiting for GPS...")

    # 订阅GPS消息
    sm = messaging.SubMaster(['gpsLocationExternal'])

    # 无限循环，直到成功同步GPS时间
    retry_count = 0
    CHECK_INTERVAL = 4  # 每4秒检查一次

    while True:
        retry_count += 1

        # 每60秒输出一次等待状态（15次 * 4秒 = 60秒）
        if retry_count % 15 == 0:
            elapsed_time = retry_count * CHECK_INTERVAL
            if not sm.valid['gpsLocationExternal']:
                cloudlog.warning(f"Waiting for GPS signal... (elapsed: {elapsed_time}s, no valid GPS data)")
            else:
                cloudlog.info(f"GPS signal detected, waiting for valid timestamp... (elapsed: {elapsed_time}s)")

        sm.update(timeout=1000)

        if sm.valid['gpsLocationExternal']:
            gps_msg = sm['gpsLocationExternal']
            gps_timestamp_ms = gps_msg.timestamp

            # 检查GPS时间是否有效
            if gps_timestamp_ms >= GPS_TIME_THRESHOLD_MS:
                cloudlog.info(f"Valid GPS time received: {gps_timestamp_ms} (after {retry_count * CHECK_INTERVAL}s)")

                # 设置系统时间
                if set_system_time_from_gps(gps_timestamp_ms):
                    # 生成正确的route_name
                    gps_time = datetime.datetime.fromtimestamp(
                        gps_timestamp_ms / 1000.0,
                        tz=datetime.timezone.utc
                    )
                    route_name = gps_time.strftime('%Y-%m-%d--%H-%M-%S')

                    # 等待2秒，让logger有机会检测到系统时间变化
                    cloudlog.info("Waiting for logger to detect time change...")
                    time.sleep(2)

                    # 重命名已创建的pending日志（只重命名本次启动后新创建的）
                    renamed = rename_pending_logs(LOG_ROOT, route_name, existing_pending_dirs)

                    cloudlog.info(f"GPS time sync completed. Route name: {route_name}, "
                                f"Renamed {renamed} log directories")

                    # 暂时注释掉进程重启逻辑
                    # 理论上 sensord 和 locationd 使用单调时钟（CLOCK_BOOTTIME），不受系统时间变化影响
                    # 如果后续测试发现仍有问题，可以取消注释恢复此功能
                    # cloudlog.info("Restarting sensord and locationd to sync with new system time...")
                    # try:
                    #     subprocess.run(['pkill', '-9', 'sensord'],
                    #                  capture_output=True, text=True, check=False)
                    #     subprocess.run(['pkill', '-9', 'locationd'],
                    #                  capture_output=True, text=True, check=False)
                    #
                    #     cloudlog.info("Sent restart signal to sensord and locationd")
                    #     cloudlog.info("Manager will automatically restart these processes with correct time")
                    #
                    #     # 等待进程重启
                    #     time.sleep(3)
                    #     cloudlog.info("Process restart completed, system should now work normally")
                    #
                    # except Exception as e:
                    #     cloudlog.error(f"Failed to restart processes: {e}")

                    cloudlog.info("Logger will automatically use correct time for new segments")
                    cloudlog.info("GPS time sync service completed successfully, exiting")
                    return  # 成功后退出
                else:
                    cloudlog.error("Failed to set system time, will retry in 10 seconds...")
                    time.sleep(10)
                    continue

        # 每次检查间隔4秒
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
