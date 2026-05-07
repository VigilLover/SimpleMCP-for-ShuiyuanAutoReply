from mcp.server.fastmcp import FastMCP
from tools.system_time import get_system_time
from tools.web_search import web_search
# from tools.read_webpage import read_webpage
from tools.fetch_webpage import fetch_webpage_content
from tools.hardware_status import get_hardware_status

import logging
import functools
import asyncio

# 简单日志配置（若主程序已配置 logging，则不会重复添加 handler）
logger = logging.getLogger("mcp_tools")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


def _wrap_tool(func):
    """返回一个包装过的工具函数，在调用时记录工具名和参数。支持同步和异步函数。"""
    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def _async_wrapper(*args, **kwargs):
            logger.info("Tool called: %s, args=%s, kwargs=%s", func.__name__, args, kwargs)
            return await func(*args, **kwargs)

        return _async_wrapper
    else:
        @functools.wraps(func)
        def _sync_wrapper(*args, **kwargs):
            logger.info("Tool called: %s, args=%s, kwargs=%s", func.__name__, args, kwargs)
            return func(*args, **kwargs)

        return _sync_wrapper

def create_mcp_server(host: str = "0.0.0.0", port: int = 58000) -> FastMCP:
    """
    创建并配置 MCP 服务器实例
    :param host: 绑定的 IP 地址
    :param port: 绑定的端口号
    :return: 配置好的 FastMCP 实例
    """
    mcp = FastMCP("AutoReplyToolServer", host=host, port=port)

    # =====================================================================
    # 注册工具 (Tool Registration)
    # =====================================================================
    # 注册系统时间工具
    mcp.tool()(_wrap_tool(get_system_time))

    # 注册硬件状态面板工具
    mcp.tool()(_wrap_tool(get_hardware_status))

    # 注册网页搜索工具
    mcp.tool()(_wrap_tool(web_search))

    # 注册网页抓取阅读工具
    # mcp.tool()(read_webpage)

    # 注册网页抓取内容工具 (转换并返回 Markdown)
    mcp.tool()(_wrap_tool(fetch_webpage_content))

    return mcp
