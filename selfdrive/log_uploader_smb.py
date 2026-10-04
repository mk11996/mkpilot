#!/usr/bin/env python3
"""
SMB日志上传服务
自动将车辆数据日志上传到SMB网络共享
"""
import os
import sys
import time
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

# 添加third_party到Python路径以导入smbprotocol
sys.path.insert(0, '/data/openpilot/third_party')

from common.params import Params
from selfdrive.swaglog import cloudlog

# 尝试导入smbprotocol
try:
    from smbclient import register_session, open_file, makedirs
    SMB_PROTOCOL_AVAILABLE = True
    cloudlog.info("smbprotocol库可用，将使用Python SMB客户端")
except ImportError:
    SMB_PROTOCOL_AVAILABLE = False
    cloudlog.warning("smbprotocol库不可用，将尝试使用系统工具")

# SMB配置（通过环境变量或参数配置）
SMB_SERVER = os.getenv('SMB_SERVER', '')  # 例如: //192.168.1.100/openpilot_logs
SMB_USERNAME = os.getenv('SMB_USERNAME', '')
SMB_PASSWORD = os.getenv('SMB_PASSWORD', '')
SMB_MOUNT_POINT = '/tmp/smb_logs'

# 日志目录
if os.path.exists('/data/logs'):
    LOG_DIR = '/data/logs'
else:
    LOG_DIR = os.path.join(str(Path.home()), '.comma', 'logs')

# 上传间隔（秒）
UPLOAD_INTERVAL = int(os.getenv('LOG_UPLOAD_INTERVAL', '300'))  # 默认5分钟
CHECK_NETWORK_INTERVAL = 30  # 检查网络间隔

def check_network():
    """检查网络连接"""
    try:
        # 尝试ping网关或DNS服务器
        result = subprocess.run(
            ['ping', '-c', '1', '-W', '2', '8.8.8.8'],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False

def load_cifs_module():
    """尝试加载CIFS内核模块"""
    try:
        # 检查模块是否已加载
        result = subprocess.run(
            ['lsmod'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if 'cifs' in result.stdout:
            return True

        # 尝试加载模块
        result = subprocess.run(
            ['modprobe', 'cifs'],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False

def mount_smb():
    """挂载SMB共享"""
    if not SMB_SERVER or not SMB_USERNAME:
        cloudlog.warning("SMB配置未设置，跳过挂载")
        return False

    try:
        # 创建挂载点
        Path(SMB_MOUNT_POINT).mkdir(parents=True, exist_ok=True)

        # 检查是否已挂载
        result = subprocess.run(
            ['mountpoint', '-q', SMB_MOUNT_POINT],
            capture_output=True
        )
        if result.returncode == 0:
            cloudlog.info("SMB已挂载")
            return True

        # 尝试加载CIFS模块
        if not load_cifs_module():
            cloudlog.warning("CIFS模块不可用，将使用smbclient模式")
            return False

        # 挂载SMB
        mount_cmd = [
            'mount', '-t', 'cifs',
            SMB_SERVER,
            SMB_MOUNT_POINT,
            '-o', f'username={SMB_USERNAME},password={SMB_PASSWORD},vers=3.0'
        ]

        result = subprocess.run(
            mount_cmd,
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            cloudlog.info(f"SMB挂载成功: {SMB_SERVER} -> {SMB_MOUNT_POINT}")
            return True
        else:
            cloudlog.error(f"SMB挂载失败: {result.stderr}")
            return False

    except Exception as e:
        cloudlog.error(f"SMB挂载异常: {e}")
        return False

def unmount_smb():
    """卸载SMB共享"""
    try:
        subprocess.run(['umount', SMB_MOUNT_POINT], timeout=5)
        cloudlog.info("SMB已卸载")
    except Exception as e:
        cloudlog.error(f"SMB卸载失败: {e}")

def get_log_files():
    """获取需要上传的日志文件"""
    log_files = []

    try:
        log_path = Path(LOG_DIR)
        if not log_path.exists():
            return log_files

        # 扫描logs目录下的所有文件（不限制文件类型）
        for file in log_path.rglob('*'):
            if file.is_file():  # 确保是文件而不是目录
                # 跳过当前正在写入的文件（最新的文件）
                age = time.time() - file.stat().st_mtime
                if age > 60:  # 至少1分钟前的文件
                    log_files.append(file)

        # 按修改时间排序
        log_files.sort(key=lambda x: x.stat().st_mtime)

        if log_files:
            cloudlog.info(f"找到 {len(log_files)} 个待上传的日志文件")
        else:
            cloudlog.debug("没有需要上传的日志文件")

    except Exception as e:
        cloudlog.error(f"获取日志文件失败: {e}")

    return log_files

def upload_file_smbprotocol(local_file):
    """使用smbprotocol库上传文件（纯Python实现）"""
    try:
        # 解析SMB服务器地址
        # 格式: //server/share/path
        smb_parts = SMB_SERVER.strip('/').split('/')
        if len(smb_parts) < 2:
            cloudlog.error(f"SMB服务器地址格式错误: {SMB_SERVER}")
            return False

        server = smb_parts[0]
        share = smb_parts[1]
        base_path = '\\'.join(smb_parts[2:]) if len(smb_parts) > 2 else ''

        # 获取文件相对路径
        log_path = Path(LOG_DIR)
        try:
            relative_path = local_file.relative_to(log_path)
        except ValueError:
            relative_path = Path(local_file.name)

        # 提取日期文件夹名（处理新旧格式和临时文件夹）
        # 新格式：2026-03-12/vehicle_data.csv
        # 旧格式：2026-03-12--0001/vehicle_data.csv
        # 临时格式：1970-01-01/vehicle_data.csv, 1970-01-02/vehicle_data.csv
        parent_folder = relative_path.parts[0] if len(relative_path.parts) > 1 else None

        if parent_folder:
            # 检查是否是临时文件夹（1970年）
            if parent_folder.startswith('1970-'):
                # 临时文件夹，使用当前日期
                date_part = datetime.now().strftime('%Y-%m-%d')
                cloudlog.warning(f"检测到临时文件夹 {parent_folder}，使用当前日期 {date_part} 上传")
            else:
                # 提取日期部分（去掉可能的序号）
                # 2026-03-12--0001 -> 2026-03-12
                # 2026-03-12 -> 2026-03-12
                date_part = parent_folder.split('--')[0] if '--' in parent_folder else parent_folder
            remote_dir = f"{base_path}\\{date_part}".replace('/', '\\')
        else:
            # 文件在根目录，使用当前日期
            date_str = datetime.now().strftime('%Y-%m-%d')
            remote_dir = f"{base_path}\\{date_str}".replace('/', '\\')

        remote_path = f"{remote_dir}\\{local_file.name}"

        # 清理路径
        remote_dir = remote_dir.strip('\\')
        remote_path = remote_path.strip('\\')

        # 构建UNC路径
        unc_path = f"\\\\{server}\\{share}\\{remote_path}"
        unc_dir = f"\\\\{server}\\{share}\\{remote_dir}"

        # 注册SMB会话（使用用户名和密码）
        register_session(server, username=SMB_USERNAME, password=SMB_PASSWORD)

        # 创建远程目录
        try:
            makedirs(unc_dir, exist_ok=True)
        except Exception as e:
            cloudlog.debug(f"创建目录失败（可能已存在）: {e}")

        # 检查远程文件是否已存在
        try:
            from smbclient import stat as smb_stat
            remote_stat = smb_stat(unc_path)
            local_size = local_file.stat().st_size
            remote_size = remote_stat.st_size

            if local_size == remote_size:
                cloudlog.debug(f"文件已存在且大小相同，跳过: {relative_path}")
                return True
            else:
                cloudlog.info(f"文件已存在但大小不同，重新上传: {relative_path}")
        except Exception:
            # 文件不存在，继续上传
            pass

        # 上传文件
        with open(local_file, 'rb') as src:
            with open_file(unc_path, mode='wb') as dst:
                shutil.copyfileobj(src, dst)

        cloudlog.info(f"上传成功(smbprotocol): {relative_path}")
        return True

    except Exception as e:
        cloudlog.error(f"smbprotocol上传异常 {local_file}: {e}")
        return False

def upload_file_smbclient(local_file):
    """使用smbclient上传文件（不需要挂载）"""
    try:
        # 解析SMB服务器地址
        # 格式: //server/share/path
        smb_parts = SMB_SERVER.strip('/').split('/')
        if len(smb_parts) < 2:
            cloudlog.error(f"SMB服务器地址格式错误: {SMB_SERVER}")
            return False

        server = smb_parts[0]
        share = smb_parts[1]
        base_path = '/'.join(smb_parts[2:]) if len(smb_parts) > 2 else ''

        # 获取文件相对路径
        log_path = Path(LOG_DIR)
        try:
            relative_path = local_file.relative_to(log_path)
        except ValueError:
            relative_path = Path(local_file.name)

        # 提取日期文件夹名（处理新旧格式和临时文件夹）
        # 新格式：2026-03-12/vehicle_data.csv
        # 旧格式：2026-03-12--0001/vehicle_data.csv
        # 临时格式：1970-01-01/vehicle_data.csv, 1970-01-02/vehicle_data.csv
        parent_folder = relative_path.parts[0] if len(relative_path.parts) > 1 else None

        if parent_folder:
            # 检查是否是临时文件夹（1970年）
            if parent_folder.startswith('1970-'):
                # 临时文件夹，使用当前日期
                date_part = datetime.now().strftime('%Y-%m-%d')
                cloudlog.warning(f"检测到临时文件夹 {parent_folder}，使用当前日期 {date_part} 上传")
            else:
                # 提取日期部分（去掉可能的序号）
                date_part = parent_folder.split('--')[0] if '--' in parent_folder else parent_folder
            remote_dir = f"{base_path}/{date_part}".replace('//', '/').strip('/')
        else:
            # 文件在根目录，使用当前日期
            date_str = datetime.now().strftime('%Y-%m-%d')
            remote_dir = f"{base_path}/{date_str}".replace('//', '/').strip('/')

        remote_path = f"{remote_dir}/{local_file.name}"

        # 使用smbclient上传
        # 先创建目录，然后上传文件
        mkdir_cmd = f'mkdir "{remote_dir}"; put "{local_file}" "{remote_path}"'

        smb_cmd = [
            'smbclient',
            f'//{server}/{share}',
            '-U', f'{SMB_USERNAME}%{SMB_PASSWORD}',
            '-c', mkdir_cmd
        ]

        result = subprocess.run(
            smb_cmd,
            capture_output=True,
            text=True,
            timeout=30
        )

        # smbclient返回0表示成功，即使mkdir失败（目录已存在）也会继续put
        if result.returncode == 0 or 'NT_STATUS_OK' in result.stdout:
            cloudlog.info(f"上传成功(smbclient): {relative_path}")
            return True
        else:
            cloudlog.error(f"上传失败(smbclient): {result.stderr}")
            return False

    except Exception as e:
        cloudlog.error(f"smbclient上传异常 {local_file}: {e}")
        return False

def upload_file(local_file, remote_dir):
    """上传单个文件到SMB"""
    try:
        # 获取文件相对于LOG_DIR的路径
        log_path = Path(LOG_DIR)
        try:
            relative_path = local_file.relative_to(log_path)
        except ValueError:
            # 如果文件不在LOG_DIR下，直接使用文件名
            relative_path = Path(local_file.name)

        # 提取日期文件夹名（处理新旧格式和临时文件夹）
        # 新格式：2026-03-12/vehicle_data.csv
        # 旧格式：2026-03-12--0001/vehicle_data.csv
        # 临时格式：1970-01-01/vehicle_data.csv, 1970-01-02/vehicle_data.csv
        parent_folder = relative_path.parts[0] if len(relative_path.parts) > 1 else None

        if parent_folder:
            # 检查是否是临时文件夹（1970年）
            if parent_folder.startswith('1970-'):
                # 临时文件夹，使用当前日期
                date_part = datetime.now().strftime('%Y-%m-%d')
                cloudlog.warning(f"检测到临时文件夹 {parent_folder}，使用当前日期 {date_part} 上传")
            else:
                # 提取日期部分（去掉可能的序号）
                date_part = parent_folder.split('--')[0] if '--' in parent_folder else parent_folder
            remote_file_dir = Path(remote_dir) / date_part
        else:
            # 文件在根目录，使用当前日期
            date_str = datetime.now().strftime('%Y-%m-%d')
            remote_file_dir = Path(remote_dir) / date_str

        remote_file_dir.mkdir(parents=True, exist_ok=True)

        # 目标文件路径
        remote_file = remote_file_dir / local_file.name

        # 如果文件已存在且大小相同，跳过
        if remote_file.exists() and remote_file.stat().st_size == local_file.stat().st_size:
            cloudlog.debug(f"文件已存在，跳过: {relative_path}")
            return True

        # 复制文件
        shutil.copy2(local_file, remote_file)
        cloudlog.info(f"上传成功: {relative_path} -> {remote_file}")

        return True

    except Exception as e:
        cloudlog.error(f"上传文件失败 {local_file}: {e}")
        return False

def upload_logs(use_smbclient=False, use_smbprotocol=False):
    """上传所有待上传的日志"""
    if not use_smbclient and not use_smbprotocol:
        if not Path(SMB_MOUNT_POINT).exists() or not Path(SMB_MOUNT_POINT).is_mount():
            cloudlog.warning("SMB未挂载，跳过上传")
            return

    log_files = get_log_files()

    if not log_files:
        cloudlog.debug("没有需要上传的日志文件")
        return

    cloudlog.info(f"开始上传 {len(log_files)} 个日志文件")

    success_count = 0
    for log_file in log_files:
        if use_smbprotocol:
            if upload_file_smbprotocol(log_file):
                success_count += 1
        elif use_smbclient:
            if upload_file_smbclient(log_file):
                success_count += 1
        else:
            if upload_file(log_file, SMB_MOUNT_POINT):
                success_count += 1

    cloudlog.info(f"上传完成: {success_count}/{len(log_files)} 个文件成功")

def main():
    """主函数"""
    cloudlog.info("SMB日志上传服务启动")

    # 检查配置
    if not SMB_SERVER:
        cloudlog.warning("SMB_SERVER未配置，服务将不会上传日志")
        cloudlog.warning("请设置环境变量: SMB_SERVER, SMB_USERNAME, SMB_PASSWORD")
        cloudlog.warning("示例: export SMB_SERVER='//192.168.1.100/openpilot_logs'")
        # 不退出，继续运行但不上传

    params = Params()
    network_available = False
    smb_mounted = False
    use_smbclient = False  # 是否使用smbclient模式
    use_smbprotocol = SMB_PROTOCOL_AVAILABLE  # 是否使用smbprotocol模式
    last_upload_time = 0
    first_upload_done = False  # 标记是否已完成首次上传

    # 显示使用的传输模式
    if use_smbprotocol:
        cloudlog.info("使用smbprotocol库进行文件传输（纯Python实现）")
    else:
        cloudlog.info("smbprotocol不可用，将尝试挂载或smbclient模式")

    while True:
        try:
            # 定期检查网络
            if not network_available:
                network_available = check_network()
                if network_available:
                    cloudlog.info("网络连接已建立")
                else:
                    time.sleep(CHECK_NETWORK_INTERVAL)
                    continue

            # 如果使用smbprotocol，跳过挂载步骤
            if not use_smbprotocol:
                # 尝试挂载SMB
                if network_available and not smb_mounted and not use_smbclient:
                    smb_mounted = mount_smb()
                    if not smb_mounted:
                        # 挂载失败，切换到smbclient模式
                        cloudlog.info("切换到smbclient模式进行文件传输")
                        use_smbclient = True

            # 网络连接后立即进行首次上传
            if network_available and not first_upload_done:
                if use_smbprotocol or smb_mounted or use_smbclient:
                    cloudlog.info("网络就绪，立即进行首次上传")
                    upload_logs(use_smbclient=use_smbclient, use_smbprotocol=use_smbprotocol)
                    last_upload_time = time.time()
                    first_upload_done = True

            # 定期上传日志
            current_time = time.time()
            if network_available and first_upload_done and (current_time - last_upload_time) >= UPLOAD_INTERVAL:
                if use_smbprotocol or smb_mounted or use_smbclient:
                    upload_logs(use_smbclient=use_smbclient, use_smbprotocol=use_smbprotocol)
                    last_upload_time = current_time
                else:
                    cloudlog.warning("SMB未就绪，跳过本次上传")

            # 休眠
            time.sleep(30)

        except KeyboardInterrupt:
            cloudlog.info("SMB日志上传服务停止")
            if smb_mounted:
                unmount_smb()
            break
        except Exception as e:
            cloudlog.error(f"SMB日志上传服务错误: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
