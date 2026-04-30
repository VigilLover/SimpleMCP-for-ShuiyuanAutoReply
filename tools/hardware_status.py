import json
import re
import subprocess
from typing import Any


def _run_command(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.stdout.strip()
    except Exception:
        return ""


def _parse_json_command(command: list[str]) -> dict[str, Any]:
    output = _run_command(command)
    if not output:
        return {}

    try:
        return json.loads(output)
    except Exception:
        return {}


def _human_bytes(value: float | int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def _clamp_percent(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(100.0, value))


def _progress_bar(percent: float | None, width: int = 24) -> str:
    if percent is None:
        return f"[{'?' * width}]"

    filled = int(round(width * percent / 100.0))
    filled = max(0, min(width, filled))
    return f"[{'█' * filled}{'░' * (width - filled)}]"


def _parse_size_token(token: str) -> int | None:
    match = re.match(r"^(\d+(?:\.\d+)?)([KMGTP])?$", token.strip(), re.IGNORECASE)
    if not match:
        return None

    value = float(match.group(1))
    unit = (match.group(2) or "B").upper()
    multipliers = {
        "B": 1,
        "K": 1024,
        "M": 1024**2,
        "G": 1024**3,
        "T": 1024**4,
        "P": 1024**5,
    }
    return int(value * multipliers.get(unit, 1))


def _fit_text(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    if max_length <= 3:
        return text[:max_length]
    return text[: max_length - 3] + "..."


def _collect_hardware_profile() -> dict[str, str]:
    hardware = _parse_json_command(["system_profiler", "SPHardwareDataType", "-json"])
    display = _parse_json_command(["system_profiler", "SPDisplaysDataType", "-json"])

    hardware_items = hardware.get("SPHardwareDataType") or []
    hardware_info = hardware_items[0] if hardware_items else {}

    display_items = display.get("SPDisplaysDataType") or []
    display_info = display_items[0] if display_items else {}

    return {
        "machine_name": str(hardware_info.get("machine_name", "N/A")),
        "machine_model": str(hardware_info.get("machine_model", "N/A")),
        "chip_type": str(hardware_info.get("chip_type", "N/A")),
        "physical_memory": str(hardware_info.get("physical_memory", "N/A")),
        "display_model": str(display_info.get("sppci_model", display_info.get("_name", "N/A"))),
        "display_cores": str(display_info.get("sppci_cores", "N/A")),
        "display_metal": str(display_info.get("spdisplays_mtlgpufamilysupport", "N/A")),
    }


def _collect_cpu_status() -> dict[str, str | float | None]:
    top_output = _run_command(["top", "-l", "1", "-n", "0"])
    cpu_usage = None
    user_usage = None
    sys_usage = None
    idle_usage = None

    cpu_match = re.search(
        r"CPU usage:\s+([\d.]+)% user,\s+([\d.]+)% sys,\s+([\d.]+)% idle",
        top_output,
    )
    if cpu_match:
        user_usage = float(cpu_match.group(1))
        sys_usage = float(cpu_match.group(2))
        idle_usage = float(cpu_match.group(3))
        cpu_usage = 100.0 - idle_usage

    cpu_model = _run_command(["sysctl", "-n", "machdep.cpu.brand_string"]) or "N/A"
    core_count = _run_command(["sysctl", "-n", "hw.ncpu"]) or "N/A"

    load_avg = "N/A"
    load_match = re.search(r"Load Avg:\s+([\d.]+),\s+([\d.]+),\s+([\d.]+)", top_output)
    if load_match:
        load_avg = f"{load_match.group(1)} / {load_match.group(2)} / {load_match.group(3)}"

    return {
        "model": cpu_model,
        "cores": core_count,
        "usage": cpu_usage,
        "user_usage": user_usage,
        "sys_usage": sys_usage,
        "idle_usage": idle_usage,
        "load_avg": load_avg,
    }


def _collect_memory_status() -> dict[str, float | int | None]:
    total_bytes_raw = _run_command(["sysctl", "-n", "hw.memsize"])
    total_bytes = int(total_bytes_raw) if total_bytes_raw.isdigit() else None

    top_output = _run_command(["top", "-l", "1", "-n", "0"])
    used_bytes = None
    free_bytes = None

    phys_match = re.search(
        r"PhysMem:\s+([\d.]+[KMGTP]?) used.*?([\d.]+[KMGTP]?) unused",
        top_output,
    )
    if phys_match:
        used_value = _parse_size_token(phys_match.group(1))
        free_value = _parse_size_token(phys_match.group(2))
        if used_value is not None:
            used_bytes = used_value
        if free_value is not None:
            free_bytes = free_value

    if used_bytes is None and total_bytes is not None:
        vm_output = _run_command(["vm_stat"])
        page_match = re.search(r"page size of (\d+) bytes", vm_output)
        free_match = re.search(r"Pages free:\s+(\d+)\.", vm_output)
        speculative_match = re.search(r"Pages speculative:\s+(\d+)\.", vm_output)
        if page_match and free_match:
            page_size = int(page_match.group(1))
            free_pages = int(free_match.group(1))
            speculative_pages = int(speculative_match.group(1)) if speculative_match else 0
            free_bytes = (free_pages + speculative_pages) * page_size
            used_bytes = max(0, total_bytes - free_bytes)

    used_percent = None
    if total_bytes and used_bytes is not None:
        used_percent = used_bytes * 100.0 / total_bytes

    return {
        "total_bytes": total_bytes,
        "used_bytes": used_bytes,
        "free_bytes": free_bytes,
        "used_percent": used_percent,
    }


def _collect_battery_status() -> dict[str, str | float | None]:
    output = _run_command(["pmset", "-g", "batt"])
    if not output:
        return {
            "power_source": "N/A",
            "percent": None,
            "state": "N/A",
            "time_remaining": "N/A",
        }

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    power_source = "N/A"
    if lines:
        source_match = re.search(r"drawing from '(.+)'", lines[0], re.IGNORECASE)
        if source_match:
            power_source = source_match.group(1)

    percent = None
    state = "N/A"
    time_remaining = "N/A"
    if len(lines) > 1:
        percent_match = re.search(r"(\d+)%", lines[1])
        if percent_match:
            percent = float(percent_match.group(1))

        state_match = re.search(r"\d+%;\s*([^;]+);", lines[1])
        if state_match:
            state = state_match.group(1).strip()

        time_match = re.search(r"\d+%;\s*[^;]+;\s*([^\s].*?)\s+present:", lines[1])
        if time_match:
            time_remaining = time_match.group(1).strip()
            if time_remaining.lower().startswith("remaining "):
                time_remaining = time_remaining[10:].strip()
            if time_remaining.lower().endswith(" remaining"):
                time_remaining = time_remaining[:-10].strip()

    return {
        "power_source": power_source,
        "percent": percent,
        "state": state,
        "time_remaining": time_remaining,
    }


def _format_row(label: str, value: str, width: int, label_width: int = 8) -> str:
    prefix = f"│ {label:<{label_width}} │ "
    suffix = "│"
    available = max(0, width - len(prefix) - len(suffix))
    clipped = _fit_text(value, available)
    row = f"{prefix}{clipped}"
    return row.ljust(width - 1) + suffix


def get_hardware_status() -> str:
    """
    获取当前电脑的实时硬件状态，并返回一个可直接展示给用户的终端风格面板。

    适用场景：
    - 用户想查看当前设备的运行负载、剩余电量、GPU/显卡信息、内存占用情况。
    - 用户要求“像终端监控面板一样”“用代码块输出”“要有进度条/占用率/硬件型号”的展示效果。
    - 需要汇总当前机器状态但不需要结构化 JSON，而是希望直接生成一段可读文本。

    输出要求：
    - 返回值始终是一个完整的 Markdown 代码块，语言标记为 text。
    - 代码块内部包含边框、标题、硬件型号、CPU/内存/GPU/电量状态、占用比例和进度条。
    - 对于无法获取的字段，统一显示为 N/A，以保持版式稳定。

    调用约束：
    - 该工具不接收任何参数。
    - 在 macOS 上信息最完整；在其他平台上可能会降级显示部分字段。
    - 若 GPU 利用率或电池细节无法从系统命令读取，也会保留面板结构并填充 N/A。

    给 AI 的调用建议：
    - 当用户明确要求“查看电脑状态”“系统监控”“硬件面板”“CPU/内存/电量/GPU 信息”时，优先调用本工具。
    - 调用后应将返回文本原样展示给用户，不要再次改写其中的代码块内容。
    """
    profile = _collect_hardware_profile()
    cpu = _collect_cpu_status()
    memory = _collect_memory_status()
    battery = _collect_battery_status()

    cpu_usage = _clamp_percent(cpu["usage"])
    memory_usage = _clamp_percent(memory["used_percent"])
    battery_percent = _clamp_percent(battery["percent"])

    width = 96
    border_top = "╭" + "─" * (width - 2) + "╮"
    border_mid = "├" + "─" * (width - 2) + "┤"
    border_bottom = "╰" + "─" * (width - 2) + "╯"
    title = "HARDWARE TELEMETRY"

    cpu_usage_text = (
        f"{cpu_usage:>6.2f}%  {_progress_bar(cpu_usage)}  user {cpu['user_usage']:.2f}%  sys {cpu['sys_usage']:.2f}%  idle {cpu['idle_usage']:.2f}%"
        if cpu_usage is not None and cpu["user_usage"] is not None and cpu["sys_usage"] is not None and cpu["idle_usage"] is not None
        else f"N/A  {_progress_bar(None)}"
    )

    memory_text = (
        f"{_human_bytes(memory['used_bytes']) if memory['used_bytes'] is not None else 'N/A'} used / {_human_bytes(memory['total_bytes']) if memory['total_bytes'] is not None else 'N/A'} total / {_human_bytes(memory['free_bytes']) if memory['free_bytes'] is not None else 'N/A'} free"
    )
    memory_usage_text = (
        f"{memory_usage:>6.2f}%  {_progress_bar(memory_usage)}"
        if memory_usage is not None
        else f"N/A  {_progress_bar(None)}"
    )

    gpu_cores_text = f"{profile['display_cores']} cores" if profile["display_cores"] != "N/A" else "N/A"
    gpu_text = f"{profile['display_model']}  ·  {gpu_cores_text}  ·  {profile['display_metal']}"

    battery_text = (
        f"{battery['percent']:.0f}%  ·  {battery['state']}  ·  {battery['power_source']}"
        if battery["percent"] is not None
        else f"N/A  ·  {battery['state']}  ·  {battery['power_source']}"
    )
    battery_usage_text = (
        f"{battery_percent:>6.2f}%  {_progress_bar(battery_percent)}  remaining {battery['time_remaining']}"
        if battery_percent is not None
        else f"N/A  {_progress_bar(None)}  remaining {battery['time_remaining']}"
    )

    lines = [
        border_top,
        f"│{title:^{width - 2}}│",
        border_mid,
        _format_row("HOST", f"{profile['machine_name']}  ·  {profile['machine_model']}  ·  {profile['chip_type']}", width),
        _format_row("CPU", f"{profile['chip_type']}  ·  {cpu['cores']} cores  ·  {cpu['model']}", width),
        _format_row("CPU%", cpu_usage_text, width),
        _format_row("LOAD", str(cpu["load_avg"]), width),
        _format_row("MEM", memory_text, width),
        _format_row("MEM%", memory_usage_text, width),
        _format_row("GPU", gpu_text, width),
        _format_row("VRAM", "N/A  (Apple Silicon unified memory / utilization probe unavailable)", width),
        _format_row("BATT", battery_text, width),
        _format_row("BATT%", battery_usage_text, width),
        border_bottom,
    ]

    panel = "\n".join(lines)
    return f"```text\n{panel}\n```"
