"""
workflow_runner.py — Pipeline / Parallel / Race orchestration для Hermes Agent

Проблема: Hermes умеет delegate_task (один subagent), но нет групповой работы.
Решение: workflow_run, который запускает пайплайн или параллельные task'и.

Установка: скопировать в ~/.hermes/tools/ или tools/ проекта.

Использование в промпте:
  workflow_run(steps=[
    {"prompt": "Собери все data-testid на странице", "agent_type": "explore"},
    {"prompt": "Заполни форму по этим data-testid", "agent_type": "general"},
  ], mode="pipeline")

  workflow_run(steps=[
    {"prompt": "Проверь цены на Amazon"},
    {"prompt": "Проверь цены на eBay"},
  ], mode="parallel")
"""

import asyncio
import json
import time
from tools.registry import registry, tool_error

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

WORKFLOW_RUN_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "enum": ["pipeline", "parallel", "race"],
            "default": "pipeline",
            "description": (
                "pipeline — шаги идут последовательно, результат прошлого → контекст следующего. "
                "parallel — все шаги одновременно, ждём все. "
                "race — все шаги одновременно, возвращаем первый успешный результат."
            )
        },
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Инструкция для шага. Для pipeline может содержать {context} — подставится результат прошлого шага."
                    },
                    "agent_type": {
                        "type": "string",
                        "default": "general",
                        "description": "Тип агента для шага (general, explore и т.д.)"
                    },
                    "label": {
                        "type": "string",
                        "description": "Метка для отчёта (необязательно)"
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "default": 300,
                        "description": "Таймаут шага в секундах"
                    }
                },
                "required": ["prompt"]
            },
            "description": "Массив шагов для выполнения. Каждый шаг — {prompt, agent_type?, label?, timeout_seconds?}."
        },
        "fail_fast": {
            "type": "boolean",
            "default": False,
            "description": "True — остановить всё при первой ошибке. False — продолжать остальные шаги."
        }
    },
    "required": ["steps"]
}

# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

async def _handle_workflow(args: dict) -> str:
    mode = args.get("mode", "pipeline")
    steps = args["steps"]
    fail_fast = args.get("fail_fast", False)
    total = len(steps)

    if not steps:
        return tool_error("steps is empty")

    report = {
        "mode": mode,
        "total": total,
        "started_at": time.time(),
        "steps": []
    }

    try:
        from agent.run_agent import run_delegate_task
    except ImportError:
        # fallback: используем delegate_task из тулов
        try:
            from delegate_tool import _handle_delegate_task as run_delegate_task
        except ImportError:
            # Упрощённый fallback
            async def run_delegate_task(prompt: str, agent_type: str = "general", context: str = None):
                from tools.registry import registry as reg
                return await reg.dispatch("delegate_task", {
                    "prompt": prompt,
                    "agent_type": agent_type,
                    "context": context
                })

    if mode == "pipeline":
        context = None
        for i, step in enumerate(steps):
            label = step.get("label", f"step_{i+1}")
            prompt = step["prompt"]
            if context is not None:
                prompt = prompt.replace("{context}", str(context)[:2000])

            try:
                result = await run_delegate_task(
                    prompt=prompt,
                    agent_type=step.get("agent_type", "general"),
                    context=context
                )
                context = result
                report["steps"].append({
                    "label": label,
                    "status": "ok",
                    "result_preview": str(result)[:200]
                })
            except Exception as e:
                report["steps"].append({
                    "label": label,
                    "status": "error",
                    "error": str(e)
                })
                if fail_fast:
                    break

    elif mode == "parallel":
        async def run_one(step):
            try:
                result = await run_delegate_task(
                    prompt=step["prompt"],
                    agent_type=step.get("agent_type", "general"),
                    context=None
                )
                return {
                    "label": step.get("label", step["prompt"][:40]),
                    "status": "ok",
                    "result_preview": str(result)[:200]
                }
            except Exception as e:
                return {
                    "label": step.get("label", step["prompt"][:40]),
                    "status": "error",
                    "error": str(e)
                }

        results = await asyncio.gather(*[run_one(s) for s in steps], return_exceptions=True)
        for r in results:
            if isinstance(r, dict):
                report["steps"].append(r)
            else:
                report["steps"].append({"status": "error", "error": str(r)})

    elif mode == "race":
        async def run_one_race(step):
            try:
                result = await run_delegate_task(
                    prompt=step["prompt"],
                    agent_type=step.get("agent_type", "general"),
                    context=None
                )
                return {"status": "ok", "label": step.get("label", step["prompt"][:40]), "result": str(result)}
            except Exception as e:
                return {"status": "error", "label": step.get("label", step["prompt"][:40]), "error": str(e)}

        tasks = [run_one_race(s) for s in steps]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()
        winner = done.pop().result()
        report["steps"].append(winner)
        report["race_winner"] = winner.get("label")
        for other in done:
            report["steps"].append(other.result())

    report["duration_seconds"] = round(time.time() - report["started_at"], 1)

    # Форматируем отчёт
    lines = [
        f"🏗️ Workflow [{mode.upper()}] — {total} steps — {report['duration_seconds']}s",
        ""
    ]
    for s in report["steps"]:
        if s["status"] == "ok":
            lines.append(f"  ✅ {s['label']}: {s.get('result_preview', '')}")
        else:
            lines.append(f"  ❌ {s['label']}: {s.get('error', 'unknown error')}")

    if "race_winner" in report:
        lines.append(f"\n  🏁 Winner: {report['race_winner']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Регистрация
# ---------------------------------------------------------------------------

registry.register(
    name="workflow_run",
    toolset="workflow",
    schema=WORKFLOW_RUN_SCHEMA,
    handler=_handle_workflow,
    check_fn=lambda: True,
    emoji="🏗️",
    description=(
        "Запустить группу task'ов в pipeline (последовательно), "
        "parallel (одновременно) или race (гонка). "
        "Для pipeline: результат прошлого шага подставляется в {context} следующего. "
        "Каждый шаг — отдельный subagent с изолированным контекстом."
    )
)
