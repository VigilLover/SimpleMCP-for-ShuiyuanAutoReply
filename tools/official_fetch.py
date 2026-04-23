import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def official_fetch(
    url: str, 
    max_length: int = 5000, 
    start_index: int = 0, 
    raw: bool = False
) -> str:
    """
    使用官方 mcp-server-fetch 工具抓取网页内容并转换为 Markdown。
    相比基础的 read_webpage 提供更好的转换和截断控制。
    当用户需要精确提取网页内容或阅读较长文章时可以使用此工具。
    
    :param url: 需要抓取的网页 URL
    :param max_length: 返回内容的最大长度 (默认: 5000)
    :param start_index: 从指定的字符索引开始提取内容 (默认: 0)
    :param raw: 是否获取未经过 Markdown 转换的原始网页内容 (默认: False)
    """
    # 配置启动参数，利用 uvx 动态执行官方 fetch server
    server_params = StdioServerParameters(
        command="uvx",
        args=[
            "mcp-server-fetch",
            "--ignore-robots-txt",
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ]
    )

    try:
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                # 初始化与子 MCP 服务器的连接
                await session.initialize()
                
                # 组装参数调用官方 fetch 工具
                arguments = {
                    "url": url,
                    "max_length": max_length,
                    "start_index": start_index,
                    "raw": raw
                }
                
                result = await session.call_tool("fetch", arguments=arguments)
                
                if not result.content:
                    return "获取失败：没有返回内容"
                
                # 提取文本内容
                texts = []
                for content in result.content:
                    if content.type == "text":
                        texts.append(content.text)
                    else:
                        texts.append(f"[{content.type} content]")
                        
                return "\n".join(texts)
    except Exception as e:
        return f"调用官方 fetch 工具失败，URL: {url}\n原因: {str(e)}\n提示: 请确保环境已安装 'uv' 命令，且网络允许执行 uvx 下载和执行 mcp-server-fetch。"
