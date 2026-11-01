"""How often does the AI get it right? Run a fixed set of Thai messages and score.

    python -m scripts.eval_ai                 # the cloud model in .env (uses ~30 calls)
    python -m scripts.eval_ai --mock          # the rule-based fallback, free
    python -m scripts.eval_ai --model gemini-3.8-flash   # try another model

"Now" is fixed (Wednesday 28 Oct 2026, 14:00 Bangkok), so the expected answers never
change. Run it before and after every prompt or model change, and compare the score.
The free tier allows 15 requests a minute: we wait between calls (--delay).
When the provider is busy (503/429) a case is tried again later; if it never
answers, it is reported as "skipped" and left out of the score - a busy server
says nothing about how well the model understands Thai.
"""

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import date, time

from app.ai.cloud import CloudProvider
from app.ai.interpreter import Plan, interpret_plan
from app.ai.mock import MockProvider
from app.ai.provider import ExtractContext, LLMProvider, OpenTask
from app.bot.commands import Command
from app.config import get_settings

TZ = "Asia/Bangkok"
CONTEXT = ExtractContext(
    today=date(2026, 10, 28),  # a Wednesday
    timezone=TZ,
    open_tasks=[OpenTask(1, "อ่าน paper"), OpenTask(3, "ส่งรายงาน")],
    now=time(14, 0),
)


@dataclass(frozen=True)
class Case:
    text: str
    action: str
    day: str | None = None  # expected due day, "YYYY-MM-DD"
    clock: str | None = None  # expected due time, "HH:MM"
    title_has: str | None = None
    task_no: int | None = None
    priority: int | None = None
    options: tuple[str, ...] = ()
    note: str = ""
    items: tuple["Case", ...] = ()  # several tasks: what each one should be, in order
    also_ok: tuple[str, ...] = ()  # other actions that are just as good an answer


# 50 cases of how people really type: short, lazy, abbreviated, no spaces, with typos.
# Dates like "5พค" or "21พย" are the most common shortcut. "Now" is fixed (see CONTEXT).
CASES = [
    # short dates and months
    Case("21พย ส่งงาน", "add", "2026-11-21", None, "ส่งงาน"),
    Case("5พค ไปหาหมอ", "add", "2027-05-05", None, "หมอ", note="May passed this year"),
    Case("15 พย ประชุม", "add", "2026-11-15", None, "ประชุม"),
    Case("1ธค จ่ายค่าเทอม", "add", "2026-12-01", None, "ค่าเทอม"),
    Case("3มค โอนเงิน", "add", "2027-01-03", None, "โอนเงิน"),
    Case("20/11 ส่งงาน", "add", "2026-11-20", None, "ส่งงาน"),
    Case("2/11 ประชุม", "add", "2026-11-02", None, "ประชุม"),
    Case("30/10 ส่งเอกสาร", "add", "2026-10-30", None, "ส่งเอกสาร"),
    Case("20/11/69 สอบ", "add", "2026-11-20", None, "สอบ"),
    Case("10 ต.ค. จ่ายบิล", "add", "2027-10-10", None, "บิล", note="passed this year"),
    Case("15พ.ย. ไปงานแต่ง", "add", "2026-11-15", None, "งานแต่ง"),
    # weekday shortcuts
    Case("พฤ ประชุม", "add", "2026-10-29", None, "ประชุม"),
    Case("ศ. ส่งงาน", "add", "2026-10-30", None, "ส่งงาน"),
    Case("จ. หน้า ประชุม", "add", "2026-11-02", None, "ประชุม"),
    Case("อา ไปวัด", "add", "2026-11-01", None, "วัด"),
    Case("อังคารหน้า ไปหาหมอ", "add", "2026-11-03", None, "หมอ"),
    Case("มรืน ส่งของ", "add", "2026-10-30", None, "ส่งของ"),
    Case("พรุ่ง ส่งงาน", "add", "2026-10-29", None, "ส่งงาน"),
    # time shortcuts
    Case("พรุ่ง 9น ประชุม", "add", "2026-10-29", "09:00", "ประชุม"),
    Case("พรุ่ง 2บ่าย ประชุม", "add", "2026-10-29", "14:00", "ประชุม"),
    Case("พรุ่ง 3โมงเย็น ดูหนัง", "add", "2026-10-29", "15:00", "ดูหนัง"),
    Case("พรุ่ง 1ทุ่ม กินข้าว", "add", "2026-10-29", "19:00", "กินข้าว"),
    Case("2ทุ่ม โทรแม่", "add", "2026-10-28", "20:00", "โทรแม่"),
    Case("พรุ่ง 9.30 ประชุม", "add", "2026-10-29", "09:30", "ประชุม"),
    Case("บ่าย3 ประชุม", "add", "2026-10-28", "15:00", "ประชุม"),
    Case("ตี5 วิ่ง", "add", "2026-10-29", "05:00", "วิ่ง", note="passed: tomorrow"),
    Case("9โมง ประชุม", "add", "2026-10-29", "09:00", "ประชุม", note="passed: tomorrow"),
    Case("1ทุ่มครึ่ง ดูบอล", "add", "2026-10-28", "19:30", "ดูบอล"),
    # finished, lazily
    Case("ส่งรายงานแล้ว", "done", task_no=3),
    Case("อ่านจบ", "done", task_no=1),
    Case("งาน3 เสร็จ", "done", task_no=3),
    Case("3เสร็จ", "done", task_no=3),
    Case("เสร็จ1", "done", task_no=1),
    Case("ส่งรายงานเสร็จ", "done", task_no=3),
    # asking, lazily
    Case("วันนี้", "today"),
    Case("งานทั้งหมด", "all"),
    Case("สัปดาห์", "week"),
    Case("ช่วย", "help"),
    Case("งาน", "unknown", also_ok=("all", "today"), note="could mean either"),
    # noise: must never become a task
    Case("ok", "unknown"),
    Case("555", "unknown"),
    Case("ขอบคุณ", "unknown"),
    Case("พรุ่ง", "unknown", note="a day with no task"),
    Case("ยกเลิก 3", "unknown", note="change: not supported yet"),
    Case("เลื่อน อ่าน paper เป็นศุกร์", "unknown", note="change: not supported yet"),
    # several tasks, typed fast
    Case(
        "พรุ่ง ส่งงาน, ศุกร์ ประชุม",
        "add",
        items=(
            Case("", "add", "2026-10-29", None, "ส่งงาน"),
            Case("", "add", "2026-10-30", None, "ประชุม"),
        ),
    ),
    Case(
        "5พค ประชุม 21พย ส่งงาน",
        "add",
        items=(
            Case("", "add", "2027-05-05", None, "ประชุม"),
            Case("", "add", "2026-11-21", None, "ส่งงาน"),
        ),
    ),
    Case("ซื้อของ ไข่ นม ขนมปัง", "add", None, None, "ซื้อ", note="one errand = one task"),
    Case(
        "9โมง ประชุม บ่ายสามส่งงาน",
        "add",
        items=(
            Case("", "add", "2026-10-29", "09:00", "ประชุม"),
            Case("", "add", "2026-10-28", "15:00", "ส่งงาน"),
        ),
    ),
    Case(
        "ส่งรายงานแล้ว พรุ่งนี้ประชุม",
        "add",
        items=(
            Case("", "done", task_no=3),
            Case("", "add", "2026-10-29", None, "ประชุม"),
        ),
    ),
]


@dataclass
class Result:
    case: Case
    ok: bool | None  # None = skipped: the provider never answered
    got: str
    why: str = ""
    problems: list[str] = field(default_factory=list)


def describe(command: Command | None) -> str:
    if command is None:
        return "unknown"
    parts = [command.action]
    if command.title:
        parts.append(repr(command.title))
    if command.due_at:
        parts.append(command.due_at.strftime("%Y-%m-%d %H:%M"))
    elif command.due_date:
        parts.append(command.due_date.isoformat())
    if command.task_no:
        parts.append(f"#{command.task_no}")
    if command.time_options:
        parts.append("/".join(command.time_options))
    if command.priority != 2:
        parts.append(f"priority {command.priority}")
    return " ".join(parts)


def describe_plan(plan: Plan) -> str:
    text = " | ".join(describe(c) for c in plan.commands) or "unknown"
    if plan.skipped:
        text += f"   (skipped {len(plan.skipped)}: {plan.skipped[0].reason})"
    return text


def check(case: Case, command: Command | None) -> list[str]:
    """What is wrong with this one answer? An empty list = correct."""
    action = command.action if command else "unknown"
    if action != case.action and action in case.also_ok:
        return []
    if action != case.action:
        return [f"action {action} (expected {case.action})"]
    if command is None:
        return []
    problems = []
    if case.action == "add":
        day = command.due_at.date() if command.due_at else command.due_date
        clock = command.due_at.strftime("%H:%M") if command.due_at else None
        if (day.isoformat() if day else None) != case.day:
            problems.append(f"day {day} (expected {case.day})")
        if clock != case.clock:
            problems.append(f"time {clock} (expected {case.clock})")
        if case.title_has and case.title_has not in (command.title or ""):
            problems.append(
                f"title {command.title!r} (expected to contain {case.title_has!r})"
            )
    if case.task_no is not None and command.task_no != case.task_no:
        problems.append(f"task #{command.task_no} (expected #{case.task_no})")
    if case.priority is not None and command.priority != case.priority:
        problems.append(f"priority {command.priority} (expected {case.priority})")
    if case.options and command.time_options != case.options:
        problems.append(f"options {command.time_options} (expected {case.options})")
    return problems


def check_plan(case: Case, plan: Plan) -> list[str]:
    """Score everything the bot would do for this message."""
    commands = plan.commands
    if case.items:  # several tasks: the right number, each one right, in order
        if len(commands) != len(case.items):
            return [f"{len(commands)} tasks (expected {len(case.items)})"]
        problems = []
        for n, (item, command) in enumerate(zip(case.items, commands, strict=True), 1):
            problems += [f"task {n}: {p}" for p in check(item, command)]
        return problems
    if len(commands) > 1:
        return [f"{len(commands)} tasks (expected 1)"]
    return check(case, commands[0] if commands else None)


async def ask(
    case: Case, provider: LLMProvider, waits: tuple[float, ...]
) -> tuple[Plan, str]:
    """One case; when the provider is busy, wait and try again (once per wait)."""
    plan, why = await interpret_plan(case.text, CONTEXT, provider)
    for wait in waits:
        if not why.startswith("provider failed"):
            break
        print(f"   ... {why}; trying again in {wait:.0f}s", flush=True)
        await asyncio.sleep(wait)
        plan, why = await interpret_plan(case.text, CONTEXT, provider)
    return plan, why


async def run(
    provider: LLMProvider,
    delay: float = 0,
    waits: tuple[float, ...] = (),
    cases: list[Case] | None = None,
) -> list[Result]:
    results = []
    for i, case in enumerate(CASES if cases is None else cases):
        if i and delay:
            await asyncio.sleep(delay)
        plan, why = await ask(case, provider, waits)
        if why.startswith("provider failed"):
            results.append(Result(case, None, "-", why))
            continue
        problems = check_plan(case, plan)
        results.append(Result(case, not problems, describe_plan(plan), why, problems))
        print(f"{'✓' if not problems else '✗'} {i + 1:02d}", flush=True)
    return results


def report(results: list[Result]) -> str:
    lines = []
    for n, r in enumerate(results, 1):
        mark = {True: "✓", False: "✗", None: "-"}[r.ok]
        lines.append(f"{mark} {n:02d} {r.case.text}")
        if r.ok is None:
            lines.append(f"      skipped ({r.why})")
        elif not r.ok:
            lines.append(f"      got: {r.got}")
            lines += [f"      - {p}" for p in r.problems]
            if r.why != "ok":
                lines.append(f"      ({r.why})")
    scored = [r for r in results if r.ok is not None]
    passed = sum(bool(r.ok) for r in scored)
    skipped = len(results) - len(scored)
    if scored:
        lines.append(f"\nผ่าน {passed}/{len(scored)} ({passed * 100 // len(scored)}%)")
    if skipped:
        lines.append(f"ข้าม {skipped} ข้อ: AI ไม่ว่าง ไม่นับคะแนน (รันใหม่ภายหลัง)")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mock", action="store_true", help="use the rule-based mock")
    parser.add_argument("--model", help="override LLM_MODEL from .env")
    parser.add_argument(
        "--delay", type=float, default=4.5, help="seconds between calls"
    )
    args = parser.parse_args()

    waits: tuple[float, ...] = ()
    if args.mock:
        provider: LLMProvider = MockProvider()
        delay = 0.0
    else:
        settings = get_settings()
        if settings.llm_api_key is None:
            raise SystemExit("Set LLM_API_KEY in .env (or use --mock)")
        model = args.model or settings.llm_model
        provider = CloudProvider(
            settings.llm_base_url,
            settings.llm_api_key.get_secret_value(),
            model,
            timeout=settings.llm_timeout_seconds,
            json_mode=settings.llm_json_mode,
        )
        delay = args.delay
        waits = (15.0, 45.0)  # a busy provider often recovers within a minute
        print(
            f"model: {model}  ({len(CASES)} calls, about {len(CASES) * delay / 60:.0f} min)\n"
        )
    print("\n" + report(asyncio.run(run(provider, delay, waits, CASES))))


if __name__ == "__main__":
    main()
