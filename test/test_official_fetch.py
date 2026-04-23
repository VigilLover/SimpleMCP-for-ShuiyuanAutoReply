import sys
import os
import asyncio

# 将项目根目录加入 sys.path 以便正常导入 tools 模块
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from tools.official_fetch import official_fetch

async def test_fetch():
    url = "https://www.weather.com.cn/weather/101020200.shtml"
    print(f"[*] 正在测试官方 fetch 工具，目标网址: {url} ...\n")
    print(f"[*] 首次运行可能会通过 uvx 下载环境，请耐心等待...\n")
    
    result = await official_fetch(url, max_length=500)
    
    print("[*] 网页全文提取测试结果 (截取前500字符): \n")
    print(result)

if __name__ == "__main__":
    asyncio.run(test_fetch())
