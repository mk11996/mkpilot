import logging
import os
import time
from pathlib import Path
from logging.handlers import BaseRotatingHandler

import zmq

from common.logging_extra import SwagLogger, SwagFormatter, SwagLogFileFormatter
from selfdrive.hardware import PC

if PC:
  SWAGLOG_DIR = os.path.join(str(Path.home()), ".comma", "log")
else:
  SWAGLOG_DIR = "/data/log/"

def get_file_handler():
  Path(SWAGLOG_DIR).mkdir(parents=True, exist_ok=True)
  base_filename = os.path.join(SWAGLOG_DIR, "swaglog")
  handler = SwaglogRotatingFileHandler(base_filename)
  return handler

class SwaglogRotatingFileHandler(BaseRotatingHandler):
  def __init__(self, base_filename, interval=60, max_bytes=1024*256, backup_count=2500, encoding=None):
    super().__init__(base_filename, mode="a", encoding=encoding, delay=True)
    self.base_filename = base_filename
    self.interval = interval # seconds
    self.max_bytes = max_bytes
    self.backup_count = backup_count
    self.log_files = self.get_existing_logfiles()

    # 解析文件索引：从 filename.序号.扩展名 或 filename.序号_时间戳.扩展名 格式中提取序号
    log_indexes = []
    for f in self.log_files:
      # 获取文件名（不含路径）
      filename = os.path.basename(f)
      parts = filename.split(".")

      # 格式1：base.序号.ext（标准格式）
      # 例如：vehicle_data.0000000001.csv
      if len(parts) >= 3 and parts[-2].isdigit():
        log_indexes.append(int(parts[-2]))

      # 格式2：base.序号_时间戳.ext（合并时添加时间戳的格式）
      # 例如：vehicle_data.0000000000_000208.csv
      elif len(parts) >= 3 and '_' in parts[-2]:
        # 分离序号和时间戳
        idx_part = parts[-2].split('_')[0]
        if idx_part.isdigit():
          log_indexes.append(int(idx_part))

      # 格式3：base.ext.序号（旧格式兼容）
      elif len(parts) >= 2 and parts[-1].isdigit():
        log_indexes.append(int(parts[-1]))

    self.last_file_idx = max(log_indexes or [-1])
    self.last_rollover = None
    self.doRollover()

  def _open(self):
    self.last_rollover = time.monotonic()
    self.last_file_idx += 1

    # 分离文件名和扩展名，将序号插入中间
    # 例如：vehicle_data.csv -> vehicle_data.0000000001.csv
    base_path = os.path.dirname(self.base_filename)
    base_name = os.path.basename(self.base_filename)

    # 分离扩展名
    if '.' in base_name:
      name_parts = base_name.rsplit('.', 1)  # 从右边分割一次
      name_without_ext = name_parts[0]
      extension = '.' + name_parts[1]
    else:
      name_without_ext = base_name
      extension = ''

    # 构建新文件名：name.序号.ext
    next_filename = os.path.join(base_path, f"{name_without_ext}.{self.last_file_idx:010}{extension}")

    stream = open(next_filename, self.mode, encoding=self.encoding)
    self.log_files.insert(0, next_filename)
    return stream

  def get_existing_logfiles(self):
    log_files = list()
    base_dir = os.path.dirname(self.base_filename)
    base_name = os.path.basename(self.base_filename)

    # 提取文件名前缀（不含扩展名）
    # 例如：vehicle_data.csv -> vehicle_data
    if '.' in base_name:
      name_prefix = base_name.rsplit('.', 1)[0]
    else:
      name_prefix = base_name

    # 扫描目录，查找所有匹配前缀的文件
    # 匹配格式：vehicle_data.序号.csv 或 vehicle_data.序号_时间戳.csv
    for fn in os.listdir(base_dir):
      # 检查文件名是否以前缀开头
      if fn.startswith(name_prefix + '.'):
        fp = os.path.join(base_dir, fn)
        if os.path.isfile(fp):
          log_files.append(fp)

    return sorted(log_files)

  def shouldRollover(self, record):
    size_exceeded = self.max_bytes > 0 and self.stream.tell() >= self.max_bytes
    time_exceeded = self.interval > 0 and self.last_rollover + self.interval <= time.monotonic()
    return size_exceeded or time_exceeded

  def doRollover(self):
    if self.stream:
      self.stream.close()
    self.stream = self._open()

    if self.backup_count > 0:
      while len(self.log_files) > self.backup_count:
        to_delete = self.log_files.pop()
        if os.path.exists(to_delete): # just being safe, should always exist
          os.remove(to_delete)

class UnixDomainSocketHandler(logging.Handler):
  def __init__(self, formatter):
    logging.Handler.__init__(self)
    self.setFormatter(formatter)
    self.pid = None

  def connect(self):
    self.zctx = zmq.Context()
    self.sock = self.zctx.socket(zmq.PUSH)
    self.sock.setsockopt(zmq.LINGER, 10)
    self.sock.connect("ipc:///tmp/logmessage")
    self.pid = os.getpid()

  def emit(self, record):
    if os.getpid() != self.pid:
      self.connect()

    msg = self.format(record).rstrip('\n')
    # print("SEND".format(repr(msg)))
    try:
      s = chr(record.levelno)+msg
      self.sock.send(s.encode('utf8'), zmq.NOBLOCK)
    except zmq.error.Again:
      # drop :/
      pass


def add_file_handler(log):
  """
  Function to add the file log handler to swaglog.
  This can be used to store logs when logmessaged is not running.
  """
  handler = get_file_handler()
  handler.setFormatter(SwagLogFileFormatter(log))
  log.addHandler(handler)


cloudlog = log = SwagLogger()
log.setLevel(logging.DEBUG)


outhandler = logging.StreamHandler()

print_level = os.environ.get('LOGPRINT', 'warning')
if print_level == 'debug':
  outhandler.setLevel(logging.DEBUG)
elif print_level == 'info':
  outhandler.setLevel(logging.INFO)
elif print_level == 'warning':
  outhandler.setLevel(logging.WARNING)

log.addHandler(outhandler)
# logs are sent through IPC before writing to disk to prevent disk I/O blocking
log.addHandler(UnixDomainSocketHandler(SwagFormatter(log)))
