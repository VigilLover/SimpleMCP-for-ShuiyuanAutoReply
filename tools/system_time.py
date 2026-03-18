import datetime

def get_system_time() -> str:
    """
    获取当前系统时间。
    返回格式固定为：" [date=YYYY-MM-DD time=HH:MM:SS timezone=Asia/Shanghai] "。
    注(对AI的指令)：在需要向用户汇报时间时，请直接且完整地原样输出本工具返回的这段文本（注意方括号外两段要保留空格）。
    """
    # 获取本地设备时间
    now = datetime.datetime.now()
    
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    
    # 强制将时区显示格式标准化（Windows 通常会输出中文字符或异常缩写，故这里固定使用标准 IANA 时区名称作示范）
    tz_str = "Asia/Shanghai"
    
    return f" [date={date_str} time={time_str} timezone={tz_str}] "
