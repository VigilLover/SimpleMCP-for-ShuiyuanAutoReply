# SimpleMCP for Shuiyuan AutoReply

基于 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 规范实现的水源社区自动回复辅助工具服务器。

## 已集成工具

1. **`get_system_time`**: 获取当前标准系统时间。
2. **`get_hardware_status`**: 以终端艺术风格展示当前电脑的 CPU、电量、GPU、内存状态与进度条。
3. **`web_search`**: 实时的互联网搜索，支持 DDG 搜索引擎。
4. **`read_webpage`**: 基础网页纯文本读取与截断处理。
5. **`official_fetch`**: 调用 Anthropic 官方的 `mcp-server-fetch` 作为内部代理模块，支持更加健壮的 Markdown 网页转换和切片功能。

## 前置依赖

本项目要求运行在 macOS/Linux 或支持的 Windows 环境，Python 版本 >= 3.11。
若要完整支持官方 `official_fetch` 桥接代理工具，系统环境需提前全局安装 `uv` 命令行工具 (`pip install uv`，或推荐按官方说明安装：https://astral.sh/)。
桥接工具会在后台通过 `uvx` 命令动态启动并托管目标官方 MCP 实例。

## 运行方式
```bash
python main.py --host 0.0.0.0 --port 58000
```
运行后，此服务以 SSE Transport 协议暴露在 `http://127.0.0.1:58000/sse`，供支持 MCP 的代理应用接入。