import os
import sys

# 将项目根目录加入 sys.path 以便正常导入 tools 模块
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from tools.hardware_status import get_hardware_status


def test_hardware_status():
    print("[*] 正在测试硬件状态面板工具...")
    result = get_hardware_status()
    print("\n[*] 测试结果:")
    print(result)


if __name__ == "__main__":
    test_hardware_status()
