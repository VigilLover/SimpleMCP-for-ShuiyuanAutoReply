from mcp.server.fastmcp import FastMCP
from tools.system_time import get_system_time

def create_mcp_server(host: str = "0.0.0.0", port: int = 58000) -> FastMCP:
    """
    创建并配置 MCP 服务器实例
    :param host: 绑定的 IP 地址
    :param port: 绑定的端口号
    :return: 配置好的 FastMCP 实例
    """
    mcp = FastMCP("SystemTimeServer", host=host, port=port)

    # =====================================================================
    # 注册工具 (Tool Registration)
    # 将 functions 添加为大模块可以调用的接口
    # =====================================================================
    mcp.tool()(get_system_time)

    # 预留添加更多工具的位置...
    # mcp.tool()(other_tool_function)

    return mcp
