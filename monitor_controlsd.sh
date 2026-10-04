#!/bin/bash
# 监控 controlsd 启动的脚本

echo "=========================================="
echo "监控 controlsd 启动过程"
echo "=========================================="

# 停止现有的 controlsd
echo "[1] 停止现有的 controlsd..."
pkill -f controlsd
sleep 2

# 检查 controlsd 是否还在运行
if pgrep -f controlsd > /dev/null; then
    echo "✗ controlsd 仍在运行，强制终止..."
    pkill -9 -f controlsd
    sleep 1
fi

echo "✓ controlsd 已停止"

# 启动 manager（会自动启动 controlsd）
echo ""
echo "[2] 启动 manager..."
sudo systemctl restart comma

echo "✓ manager 已启动，等待 controlsd 启动..."
sleep 3

# 检查 controlsd 是否启动
echo ""
echo "[3] 检查 controlsd 状态..."
if pgrep -f controlsd > /dev/null; then
    echo "✓ controlsd 进程正在运行"
    echo "  PID: $(pgrep -f controlsd)"
else
    echo "✗ controlsd 进程未运行！"
fi

# 检查 CarParams 是否被写入
echo ""
echo "[4] 检查 CarParams..."
if python3 -c "from common.params import Params; p = Params(); cp = p.get('CarParams'); print('✓ CarParams 存在，长度:', len(cp) if cp else 0)" 2>/dev/null; then
    :
else
    echo "✗ CarParams 不存在或读取失败"
fi

# 显示最近的日志
echo ""
echo "[5] 最近的 controlsd 日志："
echo "=========================================="
journalctl -u controlsd -n 20 --no-pager 2>/dev/null || echo "无法读取 journalctl 日志"

echo ""
echo "=========================================="
echo "监控完成"
echo "=========================================="
