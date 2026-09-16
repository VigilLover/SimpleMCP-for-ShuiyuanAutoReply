# SimpleMCP for Shuiyuan AutoReply

基于 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 规范实现的水源社区自动回复辅助工具服务器。

## 已集成工具

1. **`get_system_time`**: 获取当前标准系统时间。
2. **`get_hardware_status`**: 以终端艺术风格展示当前电脑的 CPU、电量、GPU、内存状态与进度条。
3. **`web_search`**: 基于 DDGS 的结构化网页与新闻搜索，支持地区、时间、分页和通用域名过滤。
4. **`image_search`**: 通用图片搜索，返回来源网页、原图、缩略图和尺寸信息，不在服务端下载图片。
5. **`read_webpage`**: 基础网页纯文本读取与截断处理（已弃用且未注册）。
6. **`fetch_webpage_content`**: 直接、流式读取公网网页，支持正文清洗、JSON 查询、字段投影和无缺口分页。

## 前置依赖

本项目要求运行在 macOS/Linux 或支持的 Windows 环境，Python 版本 >= 3.11。
网页抓取默认拒绝私网、回环、链路本地和保留地址，只允许 80/443 端口。实际 GET 和每次重定向都会重新验证，响应正文具有 5 MiB 的硬流式上限，不依赖 `Content-Length`。

`fetch_webpage_content` 默认使用 `mode="auto"`：HTML 提取主内容，JSON 优先选择最大的顶层数组，普通文本规范化空白。可传 `query` 只返回包含关键词的文本块或 JSON 项，配合 `json_path="dataList"` 和 `fields=["name", "price"]` 提取大型接口中的必要字段。返回值包含清洗后正文、总字符数、截断标志和下一偏移。

## 运行方式
```bash
python main.py --host 0.0.0.0 --port 58000
```
运行后，此服务以 SSE Transport 协议暴露在 `http://127.0.0.1:58000/sse`，供支持 MCP 的代理应用接入。
