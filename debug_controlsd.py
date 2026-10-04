#!/usr/bin/env python3
"""
调试脚本：找出 controlsd 在哪里卡住
使用方法：在设备上运行 python3 debug_controlsd.py
"""

import sys
import traceback

print("=" * 60)
print("开始调试 controlsd 初始化流程")
print("=" * 60)

try:
    print("\n[1/10] 导入基础模块...")
    from cereal import car
    from common.params import Params
    import cereal.messaging as messaging
    print("✓ 基础模块导入成功")

    print("\n[2/10] 导入 car_helpers...")
    from selfdrive.car.car_helpers import interfaces, fingerprint, get_car
    print(f"✓ car_helpers 导入成功，interfaces 包含 {len(interfaces)} 个车型")

    print("\n[3/10] 检查 MAZDA 3 是否在 interfaces 中...")
    if "MAZDA 3" in interfaces:
        print("✓ MAZDA 3 在 interfaces 字典中")
        CarInterface, CarController, CarState = interfaces["MAZDA 3"]
        print(f"  - CarInterface: {CarInterface}")
        print(f"  - CarController: {CarController}")
        print(f"  - CarState: {CarState}")
    else:
        print("✗ MAZDA 3 不在 interfaces 字典中！")
        print(f"  可用的车型: {list(interfaces.keys())}")
        sys.exit(1)

    print("\n[4/10] 测试 CarInterface.get_params...")
    from selfdrive.car import gen_empty_fingerprint
    try:
        print("  调用 CarInterface.get_params('MAZDA 3', fingerprint, [])...")
        car_params = CarInterface.get_params("MAZDA 3", gen_empty_fingerprint(), [])
        print(f"✓ get_params 成功返回")
        print(f"  - carName: {car_params.carName}")
        print(f"  - carFingerprint: {car_params.carFingerprint}")
    except Exception as e:
        print(f"✗ get_params 失败: {e}")
        traceback.print_exc()
        sys.exit(1)

    print("\n[5/10] 测试 CarInterface 实例化...")
    try:
        print("  创建 CarInterface 实例...")
        ci = CarInterface(car_params, CarController, CarState)
        print("✓ CarInterface 实例化成功")
    except Exception as e:
        print(f"✗ CarInterface 实例化失败: {e}")
        traceback.print_exc()
        sys.exit(1)

    print("\n[6/10] 检查 Params 读取...")
    try:
        params = Params()
        lateral_only = params.get_bool("LateralOnlyActive")
        print(f"✓ Params 读取成功，LateralOnlyActive = {lateral_only}")
    except Exception as e:
        print(f"✗ Params 读取失败: {e}")
        traceback.print_exc()

    print("\n[7/10] 检查 DBC 字典...")
    from selfdrive.car.mazda.values import DBC, CAR
    print(f"  DBC 字典包含 {len(DBC)} 个条目")
    if CAR.MAZDA3 in DBC:
        print(f"✓ CAR.MAZDA3 在 DBC 中")
        print(f"  CAR.MAZDA3 = {repr(CAR.MAZDA3)}")
    else:
        print(f"✗ CAR.MAZDA3 不在 DBC 中")
        print(f"  DBC 的键: {list(DBC.keys())}")

    print("\n[8/10] 测试 CarState 初始化...")
    try:
        print("  创建 CarState 实例...")
        cs = CarState(car_params)
        print("✓ CarState 初始化成功")
    except Exception as e:
        print(f"✗ CarState 初始化失败: {e}")
        traceback.print_exc()
        sys.exit(1)

    print("\n[9/10] 测试 CAN 解析器...")
    try:
        print("  调用 CarState.get_can_parser...")
        cp = CarState.get_can_parser(car_params)
        print("✓ get_can_parser 成功")
    except Exception as e:
        print(f"✗ get_can_parser 失败: {e}")
        traceback.print_exc()
        sys.exit(1)

    print("\n[10/10] 所有测试完成！")
    print("=" * 60)
    print("结论：所有初始化步骤都成功了")
    print("问题可能在运行时而不是初始化时")
    print("=" * 60)

except Exception as e:
    print(f"\n✗ 发生未捕获的异常: {e}")
    traceback.print_exc()
    sys.exit(1)
