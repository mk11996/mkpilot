import datetime

MIN_DATE = datetime.datetime(year=2020, month=1, day=1)

def system_time_valid():
  """检查系统时间是否有效（晚于2020年1月1日）"""
  return datetime.datetime.now() > MIN_DATE
