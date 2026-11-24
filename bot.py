# bot.py
import asyncio
import html
import logging
import os
import sqlite3
import time
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from agent_loop import run_agent_turn
from brain import clear_history, save_message
from personas import PERSONAS, get_mode, resolve_persona, set_mode
from tools_dev import list_backups, restore_backup

load_dotenv()
logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0") or "0")

_USER_LOCKS: dict[int, asyncio.Lock] = {}


def _lock(uid: int) -> asyncio.Lock:
    return _USER_LOCKS.setdefault(uid, asyncio.Lock())


def _is_authorized(update: Update) -> bool:
    user = update.effective_user
    return bool(user and ALLOWED_USER_ID and user.id == ALLOWED_USER_ID)


async def _send_observation(update: Update, tool_name: str, out: dict) -> None:
    diff = out.get("diff") if isinstance(out, dict) else None
    if diff:
        escaped = html.escape(str(diff)[:1800])
        await update.message.reply_html(f"<b>{html.escape(tool_name)} diff:</b>\n<pre>{escaped}</pre>")


async def _handle_pin_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    ps = context.user_data.setdefault("pin_state", {"verified_until": 0, "attempts": 0})
    expires = context.user_data.get("pin_expires", 0)
    if time.time() > expires:
        context.user_data.pop("pending_pin", None)
        await update.message.reply_text("⌛ انتهت صلاحية الرمز. أعد الطلب.")
        return True

    expected = os.getenv("SENSITIVE_FILE_PIN", "6900")
    if text.strip() == expected:
        ps["verified_until"] = time.time() + 180
        ps["attempts"] = 0
        context.user_data.pop("pending_pin", None)
        await update.message.reply_text("✅ تم التحقق. أعد إرسال طلبك.")
        return True

    ps["attempts"] = ps.get("attempts", 0) + 1
    if ps["attempts"] >= 3:
        ps["verified_until"] = 0
        context.user_data.pop("pending_pin", None)
        await update.message.reply_text("❌ 3 محاولات فاشلة. أعد الطلب.")
        return True
    await update.message.reply_text(f"❌ رمز خاطئ ({ps['attempts']}/3).")
    return True


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update) or not update.message:
        return
    user_id = update.effective_user.id
    text = update.message.text or ""

    if context.user_data.get("pending_pin"):
        if await _handle_pin_input(update, context, text):
            return

    async with _lock(user_id):
        persona = resolve_persona(user_id, text)
        await update.message.reply_text(f"{PERSONAS[persona]['emoji']} {persona}")

        pin_state = context.user_data.setdefault(
            "pin_state", {"verified_until": 0, "attempts": 0}
        )

        async def on_step(budget, tool_name: str) -> None:
            pct = int(100 * budget.steps / max(1, budget.max_steps))
            tok_pct = int(100 * budget.tokens / max(1, budget.max_tokens))
            await update.message.reply_text(
                f"⏳ {budget.steps}/{budget.max_steps} ({pct}%) · "
                f"{budget.tokens:,}/{budget.max_tokens:,} tok ({tok_pct}%) · 🔧 {tool_name}"
            )

        async def on_observation(tool_name: str, out: dict) -> None:
            await _send_observation(update, tool_name, out)

        result = await run_agent_turn(
            user_text=text,
            persona=persona,
            user_id=user_id,
            pin_state=pin_state,
            on_step=on_step,
            on_observation=on_observation,
        )

        if result.get("need_pin"):
            context.user_data["pending_pin"] = result["need_pin"]
            pin_state["attempts"] = 0
            context.user_data["pin_expires"] = time.time() + 180
            await update.message.reply_text(
                f"🔒 {result['need_pin']['tool']} — أرسل 6900 (3 محاولات / 3 دقائق)."
            )
            return

        reply_text = result.get("text") or "تم."
        save_message("user", text)
        save_message("assistant", reply_text)
        await update.message.reply_text(reply_text)


async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(f"الوضع الحالي: {get_mode(user_id)}")
        return
    try:
        m = set_mode(user_id, context.args[0])
        await update.message.reply_text(f"✅ تم ضبط الوضع على: {m}")
    except ValueError:
        await update.message.reply_text("الأنماط المتاحة: auto, operator, coder, debugger, guardian")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    clear_history()
    await update.message.reply_text("🧹 تم مسح الذاكرة.")


async def cmd_undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    if context.args:
        res = restore_backup(context.args[0])
        if res["status"] == "ok":
            await update.message.reply_text(
                f"✅ رجعنا `{res['restored']}` من `{res['from']}`.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(f"❌ {res['error']}")
        return

    res = list_backups(limit=5)
    if not res["backups"]:
        await update.message.reply_text("📭 ما كاينش نسخ احتياطية.")
        return
    lines = ["📦 آخر النسخ الاحتياطية:", ""]
    for i, b in enumerate(res["backups"], 1):
        lines.append(f"{i}. `{b['name']}` ({b['size']} bytes)")
    lines.append("\nاستعمل `/undo <bak_name>` للاسترجاع.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    conn = sqlite3.connect("memory.db")
    n_msg = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    conn.close()
    baks = list_backups(limit=1000)["count"]
    mode = get_mode(update.effective_user.id)
    model = os.getenv("LLM_MODEL", "?")
    await update.message.reply_text(
        f"📊 Messages: {n_msg}\n"
        f"📦 Backups: {baks}\n"
        f"🎭 Mode: {mode}\n"
        f"🧠 Model: `{model}`",
        parse_mode="Markdown",
    )


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    issues = []
    if not os.path.exists("memory.db"):
        issues.append("memory.db missing")
    if not os.getenv("LLM_API_KEY"):
        issues.append("LLM_API_KEY missing")
    if not os.getenv("TELEGRAM_TOKEN"):
        issues.append("TELEGRAM_TOKEN missing")
    if issues:
        await update.message.reply_text("⚠️ " + "\n⚠️ ".join(issues))
    else:
        await update.message.reply_text("✅ كلشي سليم.")


def main() -> None:
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("mode", cmd_mode))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("undo", cmd_undo))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()


if __name__ == "__main__":
    main()