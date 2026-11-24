# tools_dev.py
"""Development tools: workspace inspection, safe file edits, backups, tests, and whitelisted execution."""

from __future__ import annotations

import difflib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]

# مجلد المشروع الأساسي، مجلد النسخ الاحتياطية، وملف سجل التعديلات
WORKSPACE = Path(__file__).resolve().parent
BACKUP_DIR = WORKSPACE / "backups"
HISTORY_FILE = BACKUP_DIR / "edit_history.json"

# مجلدات يُمنع الوصول إليها أو قراءتها/التعديل فيها داخل المشروع
BLOCKED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    "backups",
}

# جذور نظام حساسة يُمنع المساس بها
BLOCKED_SYSTEM_ROOTS = (
    Path(r"C:\Windows").resolve(),
    Path(r"C:\Program Files").resolve(),
    Path(r"C:\Program Files (x86)").resolve(),
    Path(r"C:\ProgramData").resolve(),
)

# امتدادات الملفات النصية المسموح بالبحث فيها
TEXT_EXT = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".md",
    ".txt",
    ".html",
    ".css",
    ".scss",
    ".sh",
    ".bat",
    ".ps1",
    ".sql",
    ".ini",
    ".cfg",
    ".gitkeep",
}

MAX_READ_CHARS = 25000

# قائمة التطبيقات والمواقع المسموح فتحها عبر open_app
WHITELISTED_APPS: dict[str, tuple[str, str]] = {
    "calculator": ("exe", "calc.exe"),
    "calc": ("exe", "calc.exe"),
    "notepad": ("exe", "notepad.exe"),
    "paint": ("exe", "mspaint.exe"),
    "snipping tool": ("exe", "SnippingTool.exe"),
    "task manager": ("exe", "taskmgr.exe"),
    "file explorer": ("exe", "explorer.exe"),
    "explorer": ("exe", "explorer.exe"),
    "cmd": ("exe", "cmd.exe"),
    "terminal": ("exe", "wt.exe"),
    "powershell": ("exe", "powershell.exe"),
    "vscode": ("exe", "code.exe"),
    "vs code": ("exe", "code.exe"),
    "chrome": ("exe", "chrome.exe"),
    "edge": ("exe", "msedge.exe"),
    "firefox": ("exe", "firefox.exe"),
    "whatsapp": ("web", "https://web.whatsapp.com"),
    "youtube": ("web", "https://www.youtube.com"),
    "gmail": ("web", "https://mail.google.com"),
    "google": ("web", "https://www.google.com"),
    "github": ("web", "https://github.com"),
    "chatgpt": ("web", "https://chatgpt.com"),
}

APP_WHITELIST: dict[str, str] = {
    k: v[1] for k, v in WHITELISTED_APPS.items()
}

APP_ALIASES: dict[str, list[str]] = {
    "whatsapp": ["whatsapp", "واتساب", "الواتساب", "واتس اب"],
    "youtube": ["youtube", "يوتيوب", "اليوتيوب"],
    "chrome": ["chrome", "كروم", "متصفح كروم"],
    "edge": ["edge", "ايدج", "إيدج"],
    "notepad": ["notepad", "المفكرة", "مفكرة"],
    "calculator": ["calculator", "calc", "الآلة الحاسبة", "الة حاسبة"],
    "vscode": ["vscode", "vs code", "في اس كود"],
    "explorer": ["explorer", "مستعرض الملفات", "الملفات"],
}

# توصيف الأدوات بصيغة function calling للنموذج
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and directories under WORKSPACE or a specified directory path (max 100 entries, blocked dirs excluded).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path inside WORKSPACE or explicit path (default '.').",
                        "default": ".",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Substring search across text files in a directory (max 50 hits).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Substring to search for."},
                    "path": {
                        "type": "string",
                        "description": "Directory or file path to search in (default '.').",
                        "default": ".",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_text_file",
            "description": "Read a UTF-8 text file (up to 25,000 chars) with optional line range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read."},
                    "start_line": {"type": "integer", "description": "1-based start line (optional)."},
                    "end_line": {"type": "integer", "description": "1-based inclusive end line (optional)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a text file inside WORKSPACE or at an explicit allowed path with auto-backup and diff.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target file path."},
                    "content": {"type": "string", "description": "Full UTF-8 content to write."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace an exact unique string in a text file with auto-backup and diff.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target file path."},
                    "old": {"type": "string", "description": "Exact existing string to replace."},
                    "new": {"type": "string", "description": "Replacement string."},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "syntax_check",
            "description": "Compile a Python file with py_compile to verify syntax.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Python file path to check."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "undo_last_edit",
            "description": "Revert the last file edit or remove a file created by write_file.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_backups",
            "description": "List available .bak backup files in backups/.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restore_backup",
            "description": "Restore a specific .bak backup file to its target path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "backup_name": {"type": "string", "description": "Backup filename inside backups/."},
                },
                "required": ["backup_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "backup_rotation",
            "description": "Delete .bak backup files older than max_age_days.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_age_days": {
                        "type": "integer",
                        "description": "Minimum age in days before deleting (default 7).",
                        "default": 7,
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run pytest inside WORKSPACE with a 60s timeout.",
            "parameters": {
                "type": "object",
                "properties": {
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional extra pytest arguments (e.g. ['-q', 'tests/test_foo.py']).",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": "Execute a Python script inside WORKSPACE with a 30s timeout.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Python script path to execute."},
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional CLI arguments for the script.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Launch a whitelisted, registered Windows app, shortcut, or safe http/https web target.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "App name, shortcut name, or http/https URL (e.g. 'calculator', 'whatsapp', 'https://example.com').",
                    }
                },
                "required": ["name"],
            },
        },
    },
]

TOOL_DOCS: dict[str, str] = {
    "list_dir": "List files and directories under WORKSPACE or a specified path (max 100, blocked dirs excluded).",
    "search_code": "Substring search across text files in a path (max 50 hits).",
    "read_text_file": "Read a UTF-8 text file (up to 25,000 chars) with optional start_line/end_line.",
    "write_file": "Create or overwrite a text file inside WORKSPACE or at an allowed path with auto-backup and diff.",
    "edit_file": "Replace an exact unique string in a text file with auto-backup and diff.",
    "syntax_check": "Compile a Python file with py_compile to verify syntax.",
    "undo_last_edit": "Revert the last file edit or remove a file created by write_file.",
    "list_backups": "List available .bak backup files in backups/.",
    "restore_backup": "Restore a specific .bak backup file to its target path.",
    "backup_rotation": "Delete .bak backup files older than max_age_days.",
    "run_tests": "Run pytest inside WORKSPACE with a 60s timeout.",
    "run_python": "Execute a Python script inside WORKSPACE with a 30s timeout.",
    "open_app": "Launch a whitelisted, registered Windows app, shortcut, or safe http/https web target.",
}


# دوال مساعدة للتحقق من المسارات، النسخ الاحتياطي، والفروق (diff)
def _is_blocked_system_path(path: Path) -> bool:
    resolved = path.resolve()
    for root in BLOCKED_SYSTEM_ROOTS:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _is_inside_workspace(candidate: Path) -> bool:
    try:
        candidate.relative_to(WORKSPACE)
        return True
    except ValueError:
        return False


def _display_path(target: Path) -> str:
    resolved = target.resolve()
    try:
        return str(resolved.relative_to(WORKSPACE))
    except ValueError:
        return str(resolved)


def _resolve_workspace_path(rel_path: str) -> tuple[bool, Path | str]:
    if not rel_path or not isinstance(rel_path, str):
        return False, "path is required"
    raw_path = Path(rel_path.strip()).expanduser()
    if ".." in raw_path.parts:
        return False, "parent directory traversal ('..') is not allowed"

    if raw_path.is_absolute():
        candidate = raw_path.resolve()
    else:
        candidate = (WORKSPACE / raw_path).resolve()

    if _is_blocked_system_path(candidate):
        return False, f"access to system path '{candidate}' is denied"

    if candidate == WORKSPACE or _is_inside_workspace(candidate):
        rel_parts = candidate.relative_to(WORKSPACE).parts if candidate != WORKSPACE else ()
        for part in rel_parts:
            if part in BLOCKED_DIRS:
                return False, f"access to blocked directory '{part}' is denied"

    return True, candidate


def _backup(target: Path) -> Path | None:
    if not target.exists() or not target.is_file():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    resolved_target = target.resolve()
    if _is_inside_workspace(resolved_target):
        label = str(resolved_target.relative_to(WORKSPACE)).replace("\\", "/").replace("/", "__")
    else:
        label = str(resolved_target).replace(":", "").replace("\\", "/").replace("/", "__")
    stamp = int(time.time() * 1000)
    bak_path = BACKUP_DIR / f"{label}.{stamp}.bak"
    shutil.copy2(resolved_target, bak_path)
    return bak_path


def _load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def _record_history(entry: dict) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    hist = _load_history()
    hist.append(entry)
    HISTORY_FILE.write_text(json.dumps(hist[-200:], indent=2), encoding="utf-8")


def _make_diff(path_label: str, old_text: str, new_text: str) -> str:
    lines = list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=f"a/{path_label}",
            tofile=f"b/{path_label}",
            lineterm="",
        )
    )
    if not lines:
        return "(no changes)"
    return "\n".join(lines[:200])


def _is_safe_http_url(raw: str) -> str | None:
    candidate = raw.strip()
    if not candidate:
        return None
    if not (candidate.startswith("http://") or candidate.startswith("https://")):
        if "." in candidate and " " not in candidate and "/" not in candidate.split(".")[0]:
            candidate = "https://" + candidate
        else:
            return None
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def _find_windows_app_exe(app_name: str) -> str | None:
    """Look up an executable in Windows App Paths registry keys."""
    if winreg is None:
        return None
    clean = (app_name or "").strip()
    if not clean:
        return None
    exe_names = [clean] if clean.lower().endswith(".exe") else [f"{clean}.exe", clean]
    roots = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
    ]
    for hive, base_key in roots:
        for exe_name in exe_names:
            subkey = f"{base_key}\\{exe_name}"
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    val, _ = winreg.QueryValueEx(key, None)
                    if isinstance(val, str) and val.strip():
                        exe_path = val.strip().strip('"')
                        if os.path.isfile(exe_path):
                            return exe_path
            except OSError:
                continue
    return None


def _find_shortcut_or_app(app_name: str) -> str | None:
    """Search Start Menu, Desktop shortcuts (.lnk), and common app directories."""
    clean = (app_name or "").strip().lower()
    if not clean:
        return None
    tokens = [t for t in clean.replace("-", " ").replace("_", " ").split() if t]
    if not tokens:
        return None

    search_roots: list[Path] = []
    appdata = os.environ.get("APPDATA", "")
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    user_profile = os.environ.get("USERPROFILE", "")

    for base in (appdata, local_appdata):
        if base:
            p = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            search_roots.append(p)
    if program_data:
        search_roots.append(Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    if user_profile:
        search_roots.append(Path(user_profile) / "Desktop")
    if program_data:
        search_roots.append(Path(program_data) / "Desktop")

    for root in search_roots:
        if not root.is_dir():
            continue
        try:
            for item in root.rglob("*.lnk"):
                stem_l = item.stem.lower()
                if clean in stem_l or all(tok in stem_l for tok in tokens):
                    if item.is_file():
                        return str(item)
        except OSError:
            continue

    common_dirs: list[Path] = []
    if local_appdata:
        common_dirs.append(Path(local_appdata) / "Programs")
        common_dirs.append(Path(local_appdata) / "WhatsApp")
    for env_key in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        val = os.environ.get(env_key, "")
        if val:
            common_dirs.append(Path(val))

    for cdir in common_dirs:
        if not cdir.is_dir():
            continue
        try:
            for root_dir, _, files in os.walk(cdir):
                root_path = Path(root_dir)
                if len(root_path.relative_to(cdir).parts) > 3:
                    continue
                for fname in files:
                    fl = fname.lower()
                    if not fl.endswith(".exe"):
                        continue
                    stem_l = Path(fname).stem.lower()
                    if clean == stem_l or all(tok in stem_l for tok in tokens):
                        full = root_path / fname
                        if full.is_file():
                            return str(full)
        except OSError:
            continue

    return None


def _resolve_app_target(name: str) -> tuple[bool, tuple[str, str] | str]:
    """Resolve app name to ('whitelisted', cmd), ('exe', path), ('shortcut', path), or ('web', url)."""
    raw = (name or "").strip()
    key = raw.lower()
    if not key:
        return False, "app name is required"

    canonical_key = key
    for canon, aliases in APP_ALIASES.items():
        if key == canon or any(alias.lower() in key for alias in aliases):
            canonical_key = canon
            break

    if canonical_key in WHITELISTED_APPS:
        return True, WHITELISTED_APPS[canonical_key]

    candidates = [key]
    if canonical_key != key:
        candidates.insert(0, canonical_key)

    for cand in candidates:
        exe_path = _find_windows_app_exe(cand)
        if exe_path:
            return True, ("exe", exe_path)

    for cand in candidates:
        shortcut_path = _find_shortcut_or_app(cand)
        if shortcut_path:
            kind = "shortcut" if shortcut_path.lower().endswith(".lnk") else "exe"
            return True, (kind, shortcut_path)

    safe_url = _is_safe_http_url(raw)
    if safe_url:
        return True, ("web", safe_url)

    return False, f"app '{name}' is not whitelisted, registered, or a valid http/https URL"


# الأدوات الرئيسية المتاحة للتنفيذ
def list_dir(path: str = ".") -> dict:
    ok, resolved = _resolve_workspace_path(path or ".")
    if not ok:
        return {"status": "error", "error": str(resolved)}
    target = Path(resolved)
    if not target.exists():
        return {"status": "error", "error": f"path not found: {path}"}
    if target.is_file():
        return {"status": "ok", "entries": [_display_path(target)]}
    entries: list[str] = []
    for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if _is_inside_workspace(item):
            rel_parts = item.relative_to(WORKSPACE).parts
            if any(part in BLOCKED_DIRS for part in rel_parts):
                continue
        entries.append(_display_path(item))
        if len(entries) >= 100:
            break
    return {"status": "ok", "entries": entries}


def search_code(query: str, path: str = ".") -> dict:
    if not query:
        return {"status": "error", "error": "query is required"}
    ok, resolved = _resolve_workspace_path(path or ".")
    if not ok:
        return {"status": "error", "error": str(resolved)}
    root = Path(resolved)
    if not root.exists():
        return {"status": "error", "error": f"path not found: {path}"}
    candidates = [root] if root.is_file() else list(root.rglob("*"))
    hits: list[dict] = []
    for file_path in candidates:
        if not file_path.is_file():
            continue
        if _is_inside_workspace(file_path):
            rel_parts = file_path.relative_to(WORKSPACE).parts
            if any(p in BLOCKED_DIRS for p in rel_parts):
                continue
        if file_path.suffix.lower() not in TEXT_EXT and file_path.name != ".env":
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        for idx, line in enumerate(content.splitlines(), start=1):
            if query in line:
                hits.append({
                    "file": _display_path(file_path),
                    "line": idx,
                    "text": line.strip()[:200],
                })
                if len(hits) >= 50:
                    return {"status": "ok", "hits": hits}
    return {"status": "ok", "hits": hits}


def read_text_file(path: str, start_line: int | None = None, end_line: int | None = None) -> dict:
    ok, resolved = _resolve_workspace_path(path)
    if not ok:
        return {"status": "error", "error": str(resolved)}
    target = Path(resolved)
    if not target.exists() or not target.is_file():
        return {"status": "error", "error": f"file not found: {path}"}
    text = target.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    total_lines = len(lines)
    if start_line is not None or end_line is not None:
        s = max(1, int(start_line or 1))
        e = min(total_lines, int(end_line or total_lines))
        selected = "\n".join(lines[s - 1 : e]) if s <= e else ""
    else:
        selected = text
    truncated = len(selected) > MAX_READ_CHARS
    if truncated:
        selected = selected[:MAX_READ_CHARS]
    return {
        "status": "ok",
        "path": _display_path(target),
        "content": selected,
        "total_lines": total_lines,
        "truncated": truncated,
    }


def write_file(path: str, content: str) -> dict:
    ok, resolved = _resolve_workspace_path(path)
    if not ok:
        return {"status": "error", "error": str(resolved)}
    target = Path(resolved)
    existed = target.exists()
    old_text = target.read_text(encoding="utf-8", errors="replace") if existed and target.is_file() else ""
    bak_path = _backup(target) if existed else None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    disp_name = _display_path(target)
    diff = _make_diff(disp_name, old_text, content)
    _record_history({
        "action": "write_file",
        "path": disp_name,
        "created": not existed,
        "backup": bak_path.name if bak_path else None,
        "ts": int(time.time()),
    })
    return {
        "status": "ok",
        "path": disp_name,
        "created": not existed,
        "backup": bak_path.name if bak_path else None,
        "diff": diff,
    }


def edit_file(path: str, old: str, new: str) -> dict:
    ok, resolved = _resolve_workspace_path(path)
    if not ok:
        return {"status": "error", "error": str(resolved)}
    target = Path(resolved)
    if not target.is_file():
        return {"status": "error", "error": f"file not found: {path}"}
    old_text = target.read_text(encoding="utf-8", errors="replace")
    count = old_text.count(old)
    if count == 0:
        return {"status": "error", "error": "target string not found"}
    if count > 1:
        return {"status": "error", "error": f"target string matched {count} times; must be unique"}
    new_text = old_text.replace(old, new, 1)
    bak_path = _backup(target)
    target.write_text(new_text, encoding="utf-8")
    disp_name = _display_path(target)
    diff = _make_diff(disp_name, old_text, new_text)
    _record_history({
        "action": "edit_file",
        "path": disp_name,
        "backup": bak_path.name if bak_path else None,
        "ts": int(time.time()),
    })
    return {
        "status": "ok",
        "path": disp_name,
        "backup": bak_path.name if bak_path else None,
        "diff": diff,
    }


def syntax_check(path: str) -> dict:
    ok, resolved = _resolve_workspace_path(path)
    if not ok:
        return {"status": "error", "error": str(resolved)}
    target = Path(resolved)
    if not target.is_file():
        return {"status": "error", "error": f"file not found: {path}"}
    proc = subprocess.run(
        [sys.executable, "-m", "py_compile", str(target)],
        cwd=str(WORKSPACE),
        capture_output=True,
        text=True,
        timeout=15,
    )
    return {
        "status": "ok" if proc.returncode == 0 else "error",
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def undo_last_edit() -> dict:
    hist = _load_history()
    if not hist:
        return {"status": "error", "error": "no edit history available"}
    last = hist.pop()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(hist[-200:], indent=2), encoding="utf-8")

    rel_path = last.get("path", "")
    ok, resolved = _resolve_workspace_path(rel_path)
    if not ok:
        return {"status": "error", "error": str(resolved)}
    target = Path(resolved)
    bak_name = last.get("backup")

    if bak_name:
        bak_file = (BACKUP_DIR / bak_name).resolve()
        if not bak_file.is_file():
            return {"status": "error", "error": f"backup not found: {bak_name}"}
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bak_file, target)
        return {"status": "ok", "restored": rel_path, "from_backup": bak_name}

    if last.get("created") and target.exists():
        target.unlink()
        return {"status": "ok", "removed_created": rel_path}

    return {"status": "error", "error": "nothing to undo for last entry"}


def list_backups() -> dict:
    if not BACKUP_DIR.exists():
        return {"status": "ok", "backups": []}
    files = sorted(
        [p.name for p in BACKUP_DIR.iterdir() if p.is_file() and p.suffix == ".bak"],
        reverse=True,
    )
    return {"status": "ok", "backups": files}


def restore_backup(backup_name: str) -> dict:
    if not backup_name or "/" in backup_name or "\\" in backup_name or ".." in backup_name:
        return {"status": "error", "error": "invalid backup_name"}
    bak_file = (BACKUP_DIR / backup_name).resolve()
    if not bak_file.is_file():
        return {"status": "error", "error": f"backup not found: {backup_name}"}

    stem = backup_name[:-4] if backup_name.endswith(".bak") else backup_name
    parts = stem.rsplit(".", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        return {"status": "error", "error": "unrecognized backup naming format"}
    encoded_path = parts[0]
    if encoded_path.startswith("Users__"):
        target_path_str = "C:\\" + encoded_path.replace("__", "\\")
        ok, resolved = _resolve_workspace_path(target_path_str)
    else:
        rel_path = encoded_path.replace("__", "/")
        ok, resolved = _resolve_workspace_path(rel_path)
    if not ok:
        return {"status": "error", "error": str(resolved)}
    target = Path(resolved)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bak_file, target)
    return {"status": "ok", "restored": _display_path(target), "backup": backup_name}


def backup_rotation(max_age_days: int = 7) -> dict:
    if not BACKUP_DIR.exists():
        return {"status": "ok", "deleted": []}
    days = max(0, int(max_age_days))
    cutoff = time.time() - (days * 86400)
    deleted: list[str] = []
    for p in BACKUP_DIR.iterdir():
        if p.is_file() and p.suffix == ".bak" and p.stat().st_mtime < cutoff:
            p.unlink()
            deleted.append(p.name)
    return {"status": "ok", "deleted": deleted, "max_age_days": days}


def run_tests(args: list[str] | None = None) -> dict:
    extra = [str(a) for a in (args or []) if isinstance(a, (str, int, float))]
    cmd = [sys.executable, "-m", "pytest", *extra]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
            timeout=60,
        )
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "returncode": proc.returncode,
            "stdout": proc.stdout[-8000:],
            "stderr": proc.stderr[-8000:],
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "pytest timed out after 60s"}


def run_python(path: str, args: list[str] | None = None) -> dict:
    ok, resolved = _resolve_workspace_path(path)
    if not ok:
        return {"status": "error", "error": str(resolved)}
    target = Path(resolved)
    if not target.is_file():
        return {"status": "error", "error": f"script not found: {path}"}
    extra = [str(a) for a in (args or []) if isinstance(a, (str, int, float))]
    cmd = [sys.executable, str(target), *extra]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "returncode": proc.returncode,
            "stdout": proc.stdout[-8000:],
            "stderr": proc.stderr[-8000:],
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "python execution timed out after 30s"}


def open_app(name: str) -> dict:
    ok, resolved = _resolve_app_target(name)
    if not ok:
        return {"status": "error", "error": str(resolved)}
    kind, target_val = resolved  # type: ignore[misc]
    try:
        if kind == "exe":
            subprocess.Popen([target_val], cwd=str(WORKSPACE), shell=False)
        else:
            os.startfile(target_val)  # type: ignore[attr-defined]
        return {"status": "ok", "opened": target_val, "type": kind}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"failed to open '{name}': {exc}"}


# نقطة الدخول الموحدة لتنفيذ أدوات التطوير حسب الاسم
def execute_dev_tool(tool_name: str, args: dict | None = None) -> dict:
    args = args or {}
    if tool_name == "list_dir":
        return list_dir(args.get("path", "."))
    if tool_name == "search_code":
        return search_code(args.get("query", ""), args.get("path", "."))
    if tool_name == "read_text_file":
        return read_text_file(args.get("path", ""), args.get("start_line"), args.get("end_line"))
    if tool_name == "write_file":
        return write_file(args.get("path", ""), args.get("content", ""))
    if tool_name == "edit_file":
        return edit_file(args.get("path", ""), args.get("old", ""), args.get("new", ""))
    if tool_name == "syntax_check":
        return syntax_check(args.get("path", ""))
    if tool_name == "undo_last_edit":
        return undo_last_edit()
    if tool_name == "list_backups":
        return list_backups()
    if tool_name == "restore_backup":
        return restore_backup(args.get("backup_name", ""))
    if tool_name == "backup_rotation":
        return backup_rotation(args.get("max_age_days", 7))
    if tool_name == "run_tests":
        return run_tests(args.get("args"))
    if tool_name == "run_python":
        return run_python(args.get("path", ""), args.get("args"))
    if tool_name == "open_app":
        return open_app(args.get("name", ""))
    return {"status": "error", "error": f"unknown dev tool: {tool_name}"}


# قاموس تصدير الأدوات المطلوب استيراده في tools.py
DEV_TOOLS: dict[str, object] = {
    "list_dir": list_dir,
    "search_code": search_code,
    "read_text_file": read_text_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "syntax_check": syntax_check,
    "undo_last_edit": undo_last_edit,
    "list_backups": list_backups,
    "restore_backup": restore_backup,
    "backup_rotation": backup_rotation,
    "run_tests": run_tests,
    "run_python": run_python,
    "open_app": open_app,
}