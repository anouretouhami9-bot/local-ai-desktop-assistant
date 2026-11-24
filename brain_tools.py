# brain_tools.py
"""OpenAI-compatible tool schemas for OpenRouter native tool calling."""

from tools_dev import APP_WHITELIST
from tools import SAFE_COMMANDS

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "get_system_status",
        "description": "Return CPU, RAM, disk free space, and battery snapshot.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "get_top_processes",
        "description": "Top processes by RAM or CPU (max 15).",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "default": 8, "minimum": 1, "maximum": 15},
            "sort_by": {"type": "string", "enum": ["ram", "cpu"], "default": "ram"},
        }, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "read_text_file",
        "description": "Read a text/code file under BASE_DIR. Sensitive .env requires allow_sensitive=True (PIN).",
        "parameters": {"type": "object", "properties": {
            "relative_path": {"type": "string"},
            "allow_sensitive": {"type": "boolean", "default": False},
        }, "required": ["relative_path"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "List files under WORKSPACE (max 100, blocked dirs excluded).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "default": "."},
        }, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "search_code",
        "description": "Regex-free substring search across TEXT_EXT files (max 30 hits).",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "path": {"type": "string", "default": "."},
        }, "required": ["query"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create/overwrite a text file. YELLOW tier: auto-backup + diff. Max 100,000 chars.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, "required": ["path", "content"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Exact-string replace. Requires `old` appears exactly once. YELLOW tier: auto-backup + diff.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old": {"type": "string"},
            "new": {"type": "string"},
        }, "required": ["path", "old", "new"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "syntax_check",
        "description": "Python syntax check via py_compile (green tier).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
        }, "required": ["path"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "undo_last_edit",
        "description": "Revert the last edit (or delete newly-added file).",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "run_tests",
        "description": "Run pytest. RED tier: PIN 6900. Timeout 60s.",
        "parameters": {"type": "object", "properties": {
            "target": {"type": "string", "default": ""},
        }, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "run_python",
        "description": "Run a Python file. RED tier: PIN 6900. Timeout 30s.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
        }, "required": ["path"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "open_app",
        "description": "Launch a whitelisted app. RED tier: PIN 6900.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "enum": sorted(APP_WHITELIST.keys())},
        }, "required": ["name"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "run_safe_command",
        "description": "Run a whitelisted diagnostic command. RED tier: PIN 6900.",
        "parameters": {"type": "object", "properties": {
            "command_key": {"type": "string", "enum": sorted(SAFE_COMMANDS.keys())},
        }, "required": ["command_key"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "take_screenshot",
        "description": "Capture the screen and return it to Telegram.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "analyze_screen",
        "description": "Capture the screen as JPEG and analyze it with the vision model.",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string"},
        }, "required": ["question"], "additionalProperties": False},
    }},
]


def schemas_for(persona_tools: set[str]) -> list[dict]:
    """Filter schemas to only the tools allowed for a persona."""
    return [s for s in TOOL_SCHEMAS if s["function"]["name"] in persona_tools]