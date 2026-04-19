import sys
import os

# 将项目根目录加入 sys.path 以便正常导入 tools 模块
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from tools.web_search import web_search

def test_search():
    query = "2026年最新的人工智能大模型进展"
    print(f"[*] 正在尝试搜索: {query} ...\n")
    
    result = web_search(query, max_results=3)
    
    print("[*] 搜索测试结果: \n")
    print(result)

if __name__ == "__main__":
    test_search()
