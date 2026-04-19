import sys
import os

# 将项目根目录加入 sys.path 以便正常导入 tools 模块
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from tools.system_time import get_system_time

def test_time():
    print("[*] 正在测试获取系统时间工具...")
    result = get_system_time()
    print("\n[*] 测试结果:")
    print(result)
    
if __name__ == "__main__":
    test_time()
