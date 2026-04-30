from mcp.server.fastmcp import FastMCP
from tools.system_time import get_system_time
from tools.web_search import web_search
# from tools.read_webpage import read_webpage
from tools.official_fetch import official_fetch
from tools.hardware_status import get_hardware_status

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
    mcp.tool()(get_system_time)

    # 注册硬件状态面板工具
    mcp.tool()(get_hardware_status)

    # 注册网页搜索工具
    mcp.tool()(web_search)

    # 注册网页抓取阅读工具
    # mcp.tool()(read_webpage)

    # 注册官方代理 fetch 工具
    mcp.tool()(official_fetch)

    return mcp
