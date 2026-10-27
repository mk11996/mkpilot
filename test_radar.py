#!/usr/bin/env python3
"""测试雷达数据接收"""

from cereal import messaging
import time

print("=== 雷达数据测试 ===")
print("正在监听 CAN 消息和雷达数据...")
print()

# 订阅 CAN 和 radarState 消息
sm = messaging.SubMaster(['can', 'radarState'])

can_865_count = 0
radar_data_count = 0

for i in range(100):  # 运行10秒（100 * 0.1s）
    sm.update(100)

    # 检查 CAN 消息 865
    if sm.updated['can']:
        for msg in sm['can']:
            if msg.address == 865:
                can_865_count += 1
                print(f"[{i}] 收到 CAN 消息 865")

    # 检查 radarState 消息
    if sm.updated['radarState']:
        radar_data_count += 1
        radar_state = sm['radarState']
        lead_one = radar_state.leadOne

        if lead_one.dRel > 0:
            print(f"[{i}] 雷达数据: 距离={lead_one.dRel:.2f}m, 速度={lead_one.vRel:.2f}m/s")
        else:
            print(f"[{i}] 雷达数据: 无前车")

    time.sleep(0.1)

print()
print("=== 测试结果 ===")
print(f"收到 CAN 消息 865: {can_865_count} 次")
print(f"收到 radarState 更新: {radar_data_count} 次")
