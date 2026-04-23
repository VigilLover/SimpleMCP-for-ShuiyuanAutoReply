import requests
import urllib3
from bs4 import BeautifulSoup

# 禁用 requests 因为 verify=False 产生的警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def read_webpage(url: str) -> str:
    """
    抓取并读取指定网页的纯文本内容。
    当网页搜索结果(web_search)的摘要不够充分，需要进一步阅读网页全文时请传递URL请求此工具。
    :param url: 需要读取全文的网页的有效URL地址
    已废弃，建议使用官方 fetch 工具 official_fetch 替代，提供更好的转换和截断控制。
    """
    try:
        # 添加常见的 User-Agent 防止被简单的反爬拦截
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        # 添加 verify=False 忽略 SSL 证书校验错误
        res = requests.get(url, headers=headers, timeout=10, verify=False)
        res.raise_for_status()
        
        # 检查内容类型，防止乱码（例如 PDF）
        content_type = res.headers.get("Content-Type", "").lower()
        if "application/pdf" in content_type:
            return "读取失败：这是一个 PDF 文件，当前抓取工具仅支持解析普通 HTML 网页的纯文本。如果你需要阅读此 PDF，可能需要通知用户或使用其他专属工具。"
        
        # 让 requests 自动尝试识别正确的编码，避免中文乱码
        res.encoding = res.apparent_encoding
        
        soup = BeautifulSoup(res.text, "html.parser")
        
        # 移除不可见的脚本和样式内容
        for element in soup(["script", "style", "noscript", "iframe"]):
            element.extract()
            
        # 提取纯文本并以换行分隔
        text = soup.get_text(separator='\n', strip=True)
        
        # 万一有些网页字数特别巨大（比如上万字），我们可以先截取前部分，以防把 AI 上下文撑爆
        max_length = 8000
        if len(text) > max_length:
            return text[:max_length] + "\n\n... (由于篇幅限制，文章已略去后半段内容) ..."
            
        return text
    except Exception as e:
        return f"读取网页全文失败，原因: {str(e)}"
