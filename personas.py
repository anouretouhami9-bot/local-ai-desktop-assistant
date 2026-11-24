# personas.py
"""Assistant personas, mode state helpers, guardian policy, and PIN gate helpers."""

import os

DEFAULT_PERSONA = "operator"
DEFAULT_MODE = "auto"
ALLOWED_MODES = {"auto", "confirm", "strict"}

OPERATOR_SYSTEM_PROMPT = (
    "أنت مساعد ذكي سريع وعملي على نظام Windows، يشبه تجربة Siri وGoogle Assistant في المباشرة والوضوح. "
    "عندما يطلب المستخدم فتح تطبيق أو موقع (مثل واتساب، يوتيوب، متصفح، إلخ): "
    "استدعِ أداة open_app مباشرة بالاسم المطلوب كما ذكره المستخدم. "
    "ممنوع منعاً باتاً فتح explorer كبديل عندما يطلب المستخدم تطبيقاً معيناً، ولا تفتح explorer إلا إذا طلب المستخدم صراحة فتح مستعرض الملفات أو المجلدات. "
    "اجعل ردك قصيراً ومباشراً ومطابقاً للنتيجة الفعلية للأداة دون لف أو دوران."
)

CODER_SYSTEM_PROMPT = (
    "أنت مهندس برمجيات دقيق ومباشر. ركّز على الكود النظيف والآمن، ووضّح السبب والنتيجة بإيجاز دون إطالة."
)

OPERATOR_TOOLS: list[str] = [
    "get_system_status",
    "get_top_processes",
    "read_text_file",
    "take_screenshot",
    "analyze_screen",
    "open_app",
    "run_safe_command",
]

CODER_TOOLS: list[str] = [
    "list_dir",
    "search_code",
    "read_text_file",
    "write_file",
    "edit_file",
    "syntax_check",
    "undo_last_edit",
    "list_backups",
    "restore_backup",
    "backup_rotation",
    "run_tests",
    "run_python",
]

PERSONAS: dict[str, dict] = {
    "operator": {
        "emoji": "🖥️",
        "system_prompt": OPERATOR_SYSTEM_PROMPT,
        "system": OPERATOR_SYSTEM_PROMPT,
        "max_steps": 12,
        "tools": OPERATOR_TOOLS,
    },
    "coder": {
        "emoji": "💻",
        "system_prompt": CODER_SYSTEM_PROMPT,
        "system": CODER_SYSTEM_PROMPT,
        "max_steps": 12,
        "tools": CODER_TOOLS,
    },
}

PIN_REQUIRED_TOOLS: set[str] = {
    "run_tests",
    "run_python",
    "open_app",
    "run_safe_command",
}

_USER_MODES: dict[int | str, str] = {}
_USER_PERSONAS: dict[int | str, str] = {}


def get_mode(user_id: int | str | None = None) -> str:
    if user_id is None:
        return DEFAULT_MODE
    return _USER_MODES.get(user_id, DEFAULT_MODE)


def set_mode(user_id: int | str | None, mode: str) -> str:
    clean = (mode or "").strip().lower()
    if clean not in ALLOWED_MODES:
        raise ValueError(f"unsupported mode '{mode}'; allowed: {sorted(ALLOWED_MODES)}")
    if user_id is not None:
        _USER_MODES[user_id] = clean
    return clean


def resolve_persona(user_id: int | str | None = None, text: str | None = None) -> str:
    msg = (text or "").strip().lower()
    coder_keywords = (
        "كود",
        "برمجة",
        "بايثون",
        "python",
        "code",
        "refactor",
        "bug",
        "pytest",
        "مجلد",
        "ملف",
        "ملفات",
        "صايب",
        "أنشئ",
        "انشئ",
        "write_file",
        "index.html",
        "style.css",
        "app.js",
        "products.js",
    )
    if any(k in msg for k in coder_keywords):
        chosen = "coder"
    elif user_id is not None and user_id in _USER_PERSONAS:
        chosen = _USER_PERSONAS[user_id]
    else:
        chosen = DEFAULT_PERSONA

    if chosen not in PERSONAS:
        return DEFAULT_PERSONA
    if user_id is not None:
        _USER_PERSONAS[user_id] = chosen
    return chosen


def get_persona_prompt(name: str | None = None) -> str:
    key = (name or DEFAULT_PERSONA).strip().lower()
    entry = PERSONAS.get(key) or PERSONAS[DEFAULT_PERSONA]
    return str(entry.get("system_prompt") or entry.get("system") or "")


def needs_pin(tool_name: str) -> bool:
    name = (tool_name or "").strip()
    if not name:
        return False
    _ = (os.environ.get("ZBOOK_PIN") or "").strip()
    return name in PIN_REQUIRED_TOOLS


def guardian_check(persona: str, tool_name: str, args: dict | None = None) -> tuple[bool, str]:
    args = args or {}
    _ = (persona or DEFAULT_PERSONA).strip().lower()
    t_name = (tool_name or "").strip()

    if t_name == "open_app":
        app_name = str(args.get("name", "")).strip()
        if not app_name:
            return False, "app name is required"

    return True, "ok"