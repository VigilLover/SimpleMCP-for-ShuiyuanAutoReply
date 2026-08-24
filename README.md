# SimpleMCP for Shuiyuan AutoReply

基于 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 规范实现的水源社区自动回复辅助工具服务器。

## 已集成工具

1. **`get_system_time`**: 获取当前标准系统时间。
2. **`get_hardware_status`**: 以终端艺术风格展示当前电脑的 CPU、电量、GPU、内存状态与进度条。
3. **`web_search`**: 基于 DDGS 的结构化网页与新闻搜索，支持地区、时间、分页和通用域名过滤。
4. **`image_search`**: 通用图片搜索，返回来源网页、原图、缩略图和尺寸信息，不在服务端下载图片。
5. **`read_webpage`**: 基础网页纯文本读取与截断处理（已弃用且未注册）。
6. **`fetch_webpage_content`**: 调用锁定的 `mcp-server-fetch==2026.7.10` 与兼容的 `mcp==1.27.0`，支持 Markdown 转换和切片；调用前检查公网 URL、重定向和声明的响应大小。

## 前置依赖

本项目要求运行在 macOS/Linux 或支持的 Windows 环境，Python 版本 >= 3.11。
若要完整支持 `fetch_webpage_content` 桥接代理工具，系统环境需提前全局安装 `uv` 命令行工具 (`pip install uv`，或推荐按官方说明安装：https://astral.sh/)。
桥接工具会在后台通过 `uvx` 命令动态启动并托管目标官方 MCP 实例。

网页抓取默认拒绝私网、回环、链路本地和保留地址，只允许 80/443 端口。可通过 `FETCH_MAX_DOWNLOAD_BYTES` 调整声明响应大小的预检上限，默认 5 MiB。该限制依赖目标返回准确的 `Content-Length`，不是对分块响应的硬下载上限。

## 运行方式
```bash
python main.py --host 0.0.0.0 --port 58000
```
运行后，此服务以 SSE Transport 协议暴露在 `http://127.0.0.1:58000/sse`，供支持 MCP 的代理应用接入。
