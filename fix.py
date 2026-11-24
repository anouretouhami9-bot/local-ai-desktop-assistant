from pathlib import Path

p = Path("tools_dev.py")
text = p.read_text(encoding="utf-8")

# إصلاح أي اقتطاع معروف إلا كان
text = text.replace("json.loads(HISTORY_FILE", 'json.loads(HISTORY_FILE.read_text(encoding="utf-8")')
text = text.replace('return "(', 'return "(no changes)"')

marker = "def execute_dev_tool("
if marker in text:
    text = text[:text.index(marker)]

tail = '''def execute_dev_tool(tool_name: str, args: dict | None = None) -> dict:
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
'''

p.write_text(text + tail, encoding="utf-8")
print("FIXED OK")
