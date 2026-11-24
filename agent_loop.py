# agent_loop.py
"""Modular agent loop: Observe -> Act -> Verify with native tool calling."""

import json
import time
from dataclasses import dataclass, field

from brain_tools import schemas_for
from personas import guardian_check, needs_pin, PERSONAS
from brain import call_llm, get_recent_messages, get_context_summary
from tools import execute_tool


@dataclass
class Budget:
    max_steps: int = 12
    max_tokens: int = 60_000
    max_cost_usd: float = 0.50
    max_wall_s: int = 300
    steps: int = 0
    tokens: int = 0
    cost: float = 0.0
    started_at: float = field(default_factory=time.time)

    def exhausted(self) -> str | None:
        if self.steps >= self.max_steps:
            return "max_steps"
        if self.tokens >= self.max_tokens:
            return "max_tokens"
        if self.cost >= self.max_cost_usd:
            return "max_cost"
        if time.time() - self.started_at > self.max_wall_s:
            return "wall_clock"
        return None

    def add_usage(self, usage: dict) -> None:
        self.tokens += int(usage.get("total_tokens", 0) or 0)
        cost = usage.get("cost")
        if isinstance(cost, (int, float)):
            self.cost += float(cost)
        else:
            self.cost += self.tokens * 1e-6


async def run_agent_turn(
    user_text: str,
    persona: str,
    user_id: int,
    pin_state: dict,
    on_step=None,
    on_observation=None,
) -> dict:
    """
    Runs one agent turn. Returns:
      {text: str, trace: list, need_pin: dict|None, budget: Budget}
    `pin_state` shape: {"verified_until": float, "attempts": int}
    """
    cfg = PERSONAS[persona]
    budget = Budget(
        max_steps=min(cfg.get("max_steps", 12), 12),
        max_tokens=cfg.get("max_tokens", 60_000),
        max_cost_usd=cfg.get("max_cost_usd", 0.50),
    )
    allowed = set(cfg["tools"])

    messages: list[dict] = []
    summary = get_context_summary()
    if summary:
        messages.append({"role": "system", "content": f"Prior context summary:\n{summary}"})
    messages.extend(get_recent_messages(limit=8))
    messages.append({"role": "user", "content": user_text})

    trace: list[dict] = []
    system_prompt = cfg["system_prompt"]
    tools = schemas_for(allowed)

    while True:
        stop = budget.exhausted()
        if stop:
            return {
                "text": f"⏹️ توقفت الحلقة: {stop}.",
                "trace": trace,
                "need_pin": None,
                "budget": budget,
            }

        data = call_llm(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        budget.steps += 1
        budget.add_usage(data.get("usage", {}) or {})

        choice = data["choices"][0]
        msg = choice["message"]
        messages.append(msg)

        if choice.get("finish_reason") != "tool_calls" and not msg.get("tool_calls"):
            return {
                "text": msg.get("content") or "…",
                "trace": trace,
                "need_pin": None,
                "budget": budget,
            }

        for call in msg.get("tool_calls", []):
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}

            if on_step:
                await on_step(budget, name)

            ok, reason = guardian_check(persona, name, args)
            if not ok:
                out = {"status": "blocked", "reason": reason}
                print(f"[DEBUG BLOCKED] persona={persona} name={name} args={args} -> out={out}")
                messages.append(_tool_msg(call["id"], out))
                trace.append({"tool": name, "blocked": True, "reason": reason})
                if on_observation:
                    await on_observation(name, out)
                continue

            if name not in allowed:
                out = {"status": "blocked", "reason": f"{name} not in persona tools"}
                print(f"[DEBUG NOT_ALLOWED] persona={persona} name={name} -> out={out}")
                messages.append(_tool_msg(call["id"], out))
                trace.append({"tool": name, "blocked": True})
                continue

            if needs_pin(name) and not _pin_valid(pin_state):
                return {
                    "text": f"🔒 الأداة '{name}' كتطلب رمز 6900.",
                    "trace": trace,
                    "need_pin": {
                        "tool": name,
                        "args": args,
                        "tool_call_id": call["id"],
                    },
                    "budget": budget,
                }

            try:
                out = execute_tool(name, args)
            except Exception as e:  # noqa: BLE001
                out = {"status": "error", "error": f"{type(e).__name__}: {e}"}

            if not isinstance(out, dict):
                out = {"status": "ok", "output": out}

            print(f"[DEBUG TOOL] name={name} args={args} -> out={out}")

            if on_observation:
                await on_observation(name, out)

            messages.append(_tool_msg(call["id"], out))
            trace.append({"tool": name, "args": args, "ok": out.get("status") == "ok"})


def _tool_msg(tool_call_id: str, payload: dict) -> dict:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > 2500:
        text = text[:2500] + "…[truncated]"
    return {"role": "tool", "tool_call_id": tool_call_id, "content": text}


def _pin_valid(pin_state: dict) -> bool:
    return pin_state.get("verified_until", 0) > time.time()