# tools.py
"""System monitoring, safe commands, text file reading, and screen capture tools."""

import io
import logging
import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path

import psutil
from PIL import Image, ImageGrab

from brain import analyze_screenshot_with_brain

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
SCREENSHOTS_DIR = BASE_DIR / "screenshots"

MAX_READ_BYTES = 120_000
MAX_READ_CHARS = 3500
ALLOWED_TEXT_EXTENSIONS = {
    ".py",
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".csv",
    ".log",
    ".sql",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".sh",
    ".ps1",
    ".bat",
    ".env",
}

SAFE_COMMANDS: dict[str, list[str]] = {
    "ip_info": ["ipconfig"],
    "python_version": ["python", "--version"],
    "disk_check": ["powershell", "-NoProfile", "-Command", "Get-PSDrive -PSProvider FileSystem"],
}


def get_system_status() -> dict:
    """Return CPU, RAM, disk free space, and battery snapshot."""
    cpu_pct = psutil.cpu_percent(interval=0.5)
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(BASE_DIR.anchor or str(BASE_DIR))
    batt = psutil.sensors_battery()

    battery_info = None
    if batt is not None:
        battery_info = {
            "percent": round(float(batt.percent), 1),
            "plugged": bool(batt.power_plugged),
            "secs_left": batt.secsleft if batt.secsleft != psutil.POWER_TIME_UNLIMITED else None,
        }

    return {
        "status": "ok",
        "platform": platform.platform(),
        "cpu_percent": cpu_pct,
        "ram": {
            "used_mb": round(vm.used / (1024 * 1024), 1),
            "total_mb": round(vm.total / (1024 * 1024), 1),
            "percent": vm.percent,
        },
        "disk": {
            "free_gb": round(disk.free / (1024 ** 3), 2),
            "total_gb": round(disk.total / (1024 ** 3), 2),
            "percent_used": disk.percent,
        },
        "battery": battery_info,
    }


def get_top_processes(limit: int = 8, sort_by: str = "ram") -> dict:
    """Return top processes by RAM or CPU (max 15)."""
    safe_limit = max(1, min(int(limit or 8), 15))
    metric = (sort_by or "ram").lower().strip()

    procs: list[dict] = []
    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
        try:
            info = p.info
            mem_bytes = info["memory_info"].rss if info.get("memory_info") else 0
            ram_mb = round(mem_bytes / (1024 * 1024), 1)
            cpu_val = float(info.get("cpu_percent") or 0.0)
            procs.append(
                {
                    "pid": info.get("pid"),
                    "name": info.get("name") or "unknown",
                    "ram_mb": ram_mb,
                    "cpu_percent": round(cpu_val, 1),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    key_fn = (lambda x: x["cpu_percent"]) if metric == "cpu" else (lambda x: x["ram_mb"])
    procs.sort(key=key_fn, reverse=True)
    return {"status": "ok", "sort_by": metric, "processes": procs[:safe_limit]}


def read_text_file(relative_path: str, allow_sensitive: bool = False) -> dict:
    """Read a text/code file under BASE_DIR. Sensitive .env requires allow_sensitive=True."""
    if not relative_path or not isinstance(relative_path, str):
        return {"status": "error", "error": "relative_path is required"}

    target = (BASE_DIR / relative_path).resolve()
    try:
        target.relative_to(BASE_DIR)
    except ValueError:
        return {"status": "error", "error": "path escapes BASE_DIR"}

    if not target.is_file():
        return {"status": "error", "error": f"file not found: {relative_path}"}

    is_env = target.name == ".env" or target.name.startswith(".env.")
    if is_env and not allow_sensitive:
        return {"status": "requires_pin", "error": "reading .env requires PIN verification"}

    ext = target.suffix.lower()
    if not is_env and ext not in ALLOWED_TEXT_EXTENSIONS:
        return {"status": "error", "error": f"extension not allowed: {ext or 'none'}"}

    size = target.stat().st_size
    if size > MAX_READ_BYTES:
        return {"status": "error", "error": f"file too large ({size} bytes > {MAX_READ_BYTES})"}

    try:
        raw_text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed reading %s: %s", target, exc)
        return {"status": "error", "error": str(exc)}

    truncated = False
    if len(raw_text) > MAX_READ_CHARS:
        raw_text = raw_text[:MAX_READ_CHARS] + "\n…[truncated]"
        truncated = True

    return {
        "status": "ok",
        "path": str(target.relative_to(BASE_DIR)),
        "truncated": truncated,
        "content": raw_text,
    }


def run_safe_command(command_key: str) -> dict:
    """Run a whitelisted diagnostic command without shell=True."""
    key = (command_key or "").strip()
    cmd = SAFE_COMMANDS.get(key)
    if not cmd:
        return {
            "status": "error",
            "error": f"unknown command_key '{key}'. Allowed: {sorted(SAFE_COMMANDS.keys())}",
        }

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
            shell=False,
        )
        output = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
        if len(output) > 2200:
            output = output[:2200] + "\n…[truncated]"
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "command_key": key,
            "returncode": proc.returncode,
            "output": output,
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"command '{key}' timed out after 15s"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Safe command %s failed: %s", key, exc)
        return {"status": "error", "error": str(exc)}


def _grab_screen_image() -> Image.Image:
    img = ImageGrab.grab(all_screens=True)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    return img


def take_screenshot() -> dict:
    """Capture the screen and save PNG under screenshots/."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = SCREENSHOTS_DIR / f"screen_{stamp}.png"
    img = _grab_screen_image()
    img.save(out_path, format="PNG")
    return {
        "status": "ok",
        "path": str(out_path.relative_to(BASE_DIR)),
        "width": img.width,
        "height": img.height,
    }


def capture_screen_for_vision(max_width: int = 1280, quality: int = 75) -> tuple[bytes, Path]:
    """Capture screen, save PNG copy, and return compressed JPEG bytes + saved path."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    png_path = SCREENSHOTS_DIR / f"vision_{stamp}.png"

    img = _grab_screen_image()
    img.save(png_path, format="PNG")

    rgb_img = img.convert("RGB")
    if rgb_img.width > max_width:
        ratio = max_width / float(rgb_img.width)
        new_size = (max_width, max(1, int(rgb_img.height * ratio)))
        rgb_img = rgb_img.resize(new_size, Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    rgb_img.save(buf, format="JPEG", quality=max(30, min(int(quality), 95)), optimize=True)
    return buf.getvalue(), png_path


def analyze_screen(question: str = "What's on screen?") -> dict:
    """Capture screen as JPEG and analyze it with the vision model."""
    try:
        jpeg_bytes, png_path = capture_screen_for_vision()
        analysis = analyze_screenshot_with_brain(question, jpeg_bytes)
        return {
            "status": "ok",
            "screenshot": str(png_path.relative_to(BASE_DIR)),
            "analysis": analysis,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("analyze_screen failed: %s", exc)
        return {"status": "error", "error": str(exc)}


def execute_tool(tool_name: str, args: dict) -> dict:
    args = args or {}

    from tools_dev import DEV_TOOLS, execute_dev_tool

    if tool_name in DEV_TOOLS:
        res = execute_dev_tool(tool_name, args)
        return {"status": "ok", "tool": tool_name, "output": res}

    if tool_name == "get_system_status":
        return get_system_status()
    if tool_name == "get_top_processes":
        return get_top_processes(
            limit=args.get("limit", 8),
            sort_by=args.get("sort_by", "ram"),
        )
    if tool_name == "read_text_file":
        return read_text_file(
            args.get("relative_path", args.get("path", "")),
            allow_sensitive=args.get("allow_sensitive", False),
        )
    if tool_name == "run_safe_command":
        return run_safe_command(args.get("command_key", ""))
    if tool_name == "take_screenshot":
        return take_screenshot()
    if tool_name == "analyze_screen":
        return analyze_screen(args.get("question", "What's on screen?"))

    return {"status": "error", "error": f"unknown tool: {tool_name}"}