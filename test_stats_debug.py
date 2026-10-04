#!/usr/bin/env python3
"""
里程统计数据调试脚本
检查数据是否被正确保存和读取
"""
import json
from common.params import Params

def main():
    params = Params()

    print("=" * 60)
    print("里程统计数据诊断")
    print("=" * 60)

    # 检查所有统计相关的参数
    keys = ["StatsAllTime", "StatsWeek", "StatsLastReset"]

    for key in keys:
        print(f"\n【{key}】")
        try:
            # 读取原始数据
            data = params.get(key)

            if data is None:
                print("  ❌ 参数不存在或为空")
                continue

            print(f"  ✓ 数据存在")
            print(f"  - 数据类型: {type(data)}")
            print(f"  - 数据长度: {len(data)} bytes")
            print(f"  - 原始数据: {data[:100]}...")  # 只显示前100字节

            # 尝试解码
            try:
                decoded = data.decode('utf-8')
                print(f"  ✓ UTF-8 解码成功")
                print(f"  - 解码后: {decoded}")

                # 尝试解析JSON
                try:
                    parsed = json.loads(decoded)
                    print(f"  ✓ JSON 解析成功")
                    print(f"  - 解析结果: {parsed}")

                    # 检查字段
                    if key in ["StatsAllTime", "StatsWeek"]:
                        required_fields = ["routes", "distance", "minutes"]
                        for field in required_fields:
                            if field in parsed:
                                print(f"    ✓ {field}: {parsed[field]} (类型: {type(parsed[field]).__name__})")
                            else:
                                print(f"    ❌ 缺少字段: {field}")

                except json.JSONDecodeError as e:
                    print(f"  ❌ JSON 解析失败: {e}")

            except UnicodeDecodeError as e:
                print(f"  ❌ UTF-8 解码失败: {e}")

        except Exception as e:
            print(f"  ❌ 读取失败: {e}")

    # 测试写入和读取
    print("\n" + "=" * 60)
    print("测试数据写入和读取")
    print("=" * 60)

    test_data = {
        "routes": 123,
        "distance": 45678.9,
        "minutes": 987.6
    }

    print(f"\n写入测试数据: {test_data}")

    try:
        # 写入
        params.put("StatsAllTime", json.dumps(test_data).encode('utf-8'))
        print("  ✓ 写入成功")

        # 立即读取
        read_data = params.get("StatsAllTime")
        if read_data:
            decoded = read_data.decode('utf-8')
            parsed = json.loads(decoded)
            print(f"  ✓ 读取成功: {parsed}")

            # 验证数据一致性
            if parsed == test_data:
                print("  ✓ 数据一致性验证通过")
            else:
                print(f"  ❌ 数据不一致！")
                print(f"    期望: {test_data}")
                print(f"    实际: {parsed}")
        else:
            print("  ❌ 读取失败：返回 None")

    except Exception as e:
        print(f"  ❌ 测试失败: {e}")

    print("\n" + "=" * 60)
    print("诊断完成")
    print("=" * 60)

if __name__ == "__main__":
    main()
