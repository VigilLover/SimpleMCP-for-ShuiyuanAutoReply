import sys
import os

# 将项目根目录加入 sys.path 以便正常导入 tools 模块
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from tools.read_webpage import read_webpage

def test_read():
    # 使用一个简单的公共示例网站测试抓取效果
    url = "https://example.com"
    print(f"[*] 正在测试读取网页全文工具，目标网址: {url} ...\n")
    
    result = read_webpage(url)
    
    print("[*] 网页全文提取测试结果 (截取前500字符): \n")
    print(result[:500])

if __name__ == "__main__":
    test_read()
