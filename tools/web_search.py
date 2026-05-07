from ddgs import DDGS

def web_search(query: str, max_results: int = 10) -> str:
    """
    搜索互联网获取实时信息。
    当用户询问最新新闻、事实或者需要检索最新信息时，请调用此工具。
    如果要求更详细的网页内容，请使用 official_fetch 工具抓取相关网页。
    :param query: 搜索关键词
    :param max_results: 返回结果的最大数量，默认为10
    """
    try:
        results = DDGS().text(query, max_results=max_results)
        if not results:
            return "未找到相关结果。"
        
        formatted_result = []
        for r in results:
            title = r.get('title', '无标题')
            url = r.get('href', r.get('link', '无链接'))
            body = r.get('body', r.get('snippet', '无摘要'))
            formatted_result.append(f"Title: {title}\nURL: {url}\nSummary: {body}")
            
        return "\n\n---\n\n".join(formatted_result)
    except Exception as e:
        return f"网络搜索出错: {str(e)}"
