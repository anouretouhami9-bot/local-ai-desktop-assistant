# brain.py
"""Memory, summarization, native tool calling, and vision analysis."""

import base64
import logging
import os
import sqlite3
from pathlib import Path
import requests

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent / "memory.db"
SUMMARIZE_THRESHOLD = 14
SUMMARIZE_BATCH_SIZE = 8


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS context_summary ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), summary TEXT NOT NULL)"
    )
    conn.commit()
    return conn


def save_message(role: str, content: str) -> None:
    if not content:
        return
    conn = _get_conn()
    try:
        conn.execute("INSERT INTO messages (role, content) VALUES (?, ?)", (role, content))
        conn.commit()
        _maybe_summarize_history(conn)
    finally:
        conn.close()


def get_recent_messages(limit: int = 8) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]
    finally:
        conn.close()


def get_context_summary() -> str:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT summary FROM context_summary WHERE id = 1").fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


def _fallback_summarize(existing: str, batch: list[tuple[str, str]]) -> str:
    lines = [f"{role}: {content[:200]}" for role, content in batch]
    combined = "\n".join(lines)
    return f"{existing}\n{combined}".strip()[-2500:]


def _maybe_summarize_history(conn: sqlite3.Connection) -> None:
    total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    if total <= SUMMARIZE_THRESHOLD:
        return

    rows = conn.execute(
        "SELECT id, role, content FROM messages ORDER BY id ASC LIMIT ?",
        (SUMMARIZE_BATCH_SIZE,),
    ).fetchall()
    if not rows:
        return

    ids = [r[0] for r in rows]
    batch = [(r[1], r[2]) for r in rows]
    existing = get_context_summary()

    api_key = os.getenv("LLM_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    model = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")

    summary_text = ""
    if api_key:
        try:
            prompt = (
                "Summarize the conversation concisely in Arabic or match the user's language, "
                "preserving key facts, file paths, decisions, and pending goals without storing secrets.\n"
                f"Previous summary:\n{existing or '(none)'}\n\n"
                "New messages:\n" + "\n".join(f"{role}: {c[:500]}" for role, c in batch)
            )
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 600,
                },
                timeout=45,
            )
            resp.raise_for_status()
            summary_text = (
                resp.json().get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            ).strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Context summarization fallback triggered: %s", exc)

    if not summary_text:
        summary_text = _fallback_summarize(existing, batch)

    conn.execute(
        "INSERT INTO context_summary (id, summary) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET summary = excluded.summary",
        (summary_text,),
    )
    placeholders = ",".join("?" for _ in ids)
    conn.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", ids)
    conn.commit()


def clear_history() -> None:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM context_summary")
        conn.commit()
    finally:
        conn.close()


def call_llm(
    system_prompt: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_choice: str = "auto",
) -> dict:
    """One OpenRouter call. Returns the raw JSON response."""
    api_key = os.getenv("LLM_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    model = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")

    full_messages = [{"role": "system", "content": system_prompt}] + messages
    base_payload: dict = {
        "model": model,
        "messages": full_messages,
        "max_tokens": 4096,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    if tools:
        payload_with_parallel = {
            **base_payload,
            "tools": tools,
            "tool_choice": tool_choice,
            "parallel_tool_calls": True,
        }
        r = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload_with_parallel,
            timeout=90,
        )
        if r.status_code == 400 and "parallel_tool_calls" in r.text.lower():
            payload_fallback = {
                **base_payload,
                "tools": tools,
                "tool_choice": tool_choice,
                "parallel_tool_calls": False,
            }
            r = requests.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload_fallback,
                timeout=90,
            )
    else:
        r = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=base_payload,
            timeout=90,
        )

    r.raise_for_status()
    return r.json()


def analyze_screenshot_with_brain(question: str, jpeg_bytes: bytes) -> str:
    api_key = os.getenv("LLM_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    model = os.getenv("LLM_VISION_MODEL") or os.getenv("LLM_MODEL", "openai/gpt-4o-mini")

    if not api_key:
        return "LLM_API_KEY غير مضبوط لتحليل الصورة."

    b64_img = base64.b64encode(jpeg_bytes).decode("ascii")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question or "What is visible on this screen?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"},
                    },
                ],
            }
        ],
        "max_tokens": 1200,
    }
    try:
        r = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=90,
        )
        r.raise_for_status()
        content = (
            r.json().get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        ).strip()
        answer = content or "تعذر استخراج وصف واضح من الصورة."
    except Exception as exc:  # noqa: BLE001
        logger.warning("Vision request failed: %s", exc)
        answer = f"تعذر تحليل الشاشة حالياً: {exc}"

    save_message("assistant", f"[Screen analysis: {question}] {answer}")
    return answer