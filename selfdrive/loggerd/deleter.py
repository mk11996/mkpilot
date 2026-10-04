#!/usr/bin/env python3
import os
import shutil
import threading
from selfdrive.swaglog import cloudlog
from selfdrive.loggerd.config import ROOT, get_available_bytes, get_available_percent
from selfdrive.loggerd.uploader import listdir_by_creation
from common.time import system_time_valid

MIN_BYTES = 5 * 1024 * 1024 * 1024
MIN_PERCENT = 10

DELETE_LAST = ['boot', 'crash']

# 保护1970年的日志目录（系统时间无效时创建的日志）
# 当系统时间恢复正常后，这些日志会被正常删除
PROTECT_INVALID_TIME_LOGS = True


def deleter_thread(exit_event):
  while not exit_event.is_set():
    out_of_bytes = get_available_bytes(default=MIN_BYTES + 1) < MIN_BYTES
    out_of_percent = get_available_percent(default=MIN_PERCENT + 1) < MIN_PERCENT

    if out_of_percent or out_of_bytes:
      # 检查系统时间是否有效
      time_valid = system_time_valid()
      if not time_valid:
        cloudlog.warning("System time invalid (likely 1970), skipping log deletion to prevent data loss")
        # 系统时间无效时，等待更长时间再检查（避免频繁日志）
        exit_event.wait(60)
        continue

      # remove the earliest directory we can
      dirs = sorted(listdir_by_creation(ROOT), key=lambda x: x in DELETE_LAST)
      for delete_dir in dirs:
        delete_path = os.path.join(ROOT, delete_dir)

        # 额外保护：跳过1970年的日志目录（即使系统时间已恢复）
        # 这些日志可能包含重要数据，只有在空间严重不足时才删除
        if PROTECT_INVALID_TIME_LOGS and delete_dir.startswith("1970-"):
          cloudlog.info(f"Skipping 1970 log directory (invalid time): {delete_path}")
          continue

        if any(name.endswith(".lock") for name in os.listdir(delete_path)):
          continue

        try:
          cloudlog.info(f"deleting {delete_path}")
          shutil.rmtree(delete_path)
          break
        except OSError:
          cloudlog.exception(f"issue deleting {delete_path}")
      exit_event.wait(.1)
    else:
      exit_event.wait(30)


def main():
  deleter_thread(threading.Event())


if __name__ == "__main__":
  main()
