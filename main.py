import argparse

from core.mcp_server import create_mcp_server

def main():
    # 使用 argparse 来支持使用命令行传参启动，方便根据环境修改端口防止 10013 错误
    parser = argparse.ArgumentParser(description="运行 MCP (Model Context Protocol) 扩展服务器。")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="要绑定的 IP 地址 (默认 0.0.0.0)")
    parser.add_argument("--port", type=int, default=58000, help="要绑定的端口 (默认使用较高段的 58000)")
    
    args = parser.parse_args()

    # 从 core 中获取实例化并注册好工具的 FastMCP 服务器
    mcp = create_mcp_server(host=args.host, port=args.port)

    # 启动服务器并在控制台输出信息
    print(f"===========================================================")
    print(f"Starting MCP Server (SSE Mode) on http://{args.host}:{args.port}/sse")
    print(f"===========================================================")
    
    # 按照客户端通常要求，推荐启动 SSE 类型的 Transport
    mcp.run("sse")

if __name__ == '__main__':
    main()


