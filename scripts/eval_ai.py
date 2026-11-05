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
    open_tasks=[
        OpenTask(1, "อ่าน paper"),
        OpenTask(3, "ส่งรายงาน", date(2026, 10, 30), time(14, 0)),
    ],
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


# 50 cases of how people really type: short, lazy, abbreviated, no spaces, several at once.
# None of them may be copied into the system prompt as an example: a case the model has
# already seen the answer to measures nothing (tests/test_eval_ai.py checks this).
# "Now" is fixed (see CONTEXT): Wednesday 2026-10-28, 14:00.
CASES = [
    # short dates and months
    Case("3ธค จ่ายค่าเทอมลูก", "add", "2026-12-03", None, "ค่าเทอม"),
    Case("12มค ต่อประกัน", "add", "2027-01-12", None, "ประกัน", note="passed: next year"),
    Case("8สค ไปงานบวช", "add", "2027-08-08", None, "งานบวช", note="passed: next year"),
    Case("25 พย ส่งแบบ", "add", "2026-11-25", None, "ส่งแบบ"),
    Case("20/11/68 ประชุม", "unknown", note="a past year"),
    Case("15/11/69 ประชุมใหญ่", "add", "2026-11-15", None, "ประชุมใหญ่"),
    Case("ส่งใบเสนอราคา 30พย", "add", "2026-11-30", None, "ใบเสนอราคา"),
    Case("31ตค ปาร์ตี้ฮาโลวีน", "add", "2026-10-31", None, "ฮาโลวีน"),
    # weekday and day shortcuts
    Case("พุธหน้า ส่งสไลด์", "add", "2026-11-04", None, "สไลด์"),
    Case("ส. ไปตลาดนัด", "add", "2026-10-31", None, "ตลาดนัด"),
    Case("ศุกร์นี้ ส่งงวด 2", "add", "2026-10-30", None, "งวด"),
    Case("มะรืน ซักผ้าห่ม", "add", "2026-10-30", None, "ซักผ้า"),
    Case("อีก 3 วัน คืนหนังสือ", "add", "2026-10-31", None, "หนังสือ"),
    Case("สิ้นเดือน ต่อ พรบ.", "add", "2026-10-31", None, "พรบ"),
    # time shortcuts
    Case("พรุ่งนี้ 8น ซ้อมใหญ่", "add", "2026-10-29", "08:00", "ซ้อม"),
    Case("พรุ่งนี้ 4 โมงเย็น รับของ", "add", "2026-10-29", "16:00", "รับของ"),
    Case("พรุ่ง 3ทุ่ม โทรหาพี่", "add", "2026-10-29", "21:00", "โทรหาพี่"),
    Case(
        "ตี 4 ไปสนามบิน", "add", "2026-10-29", "04:00", "สนามบิน", note="passed: tomorrow"
    ),
    Case("บ่าย 4 โมงครึ่ง นัดลูกค้า", "add", "2026-10-28", "16:30", "นัดลูกค้า"),
    Case("5ทุ่ม ปิดบัญชี", "add", "2026-10-28", "23:00", "ปิดบัญชี"),
    Case(
        "10โมงเช้า ประชุมสาย",
        "add",
        "2026-10-29",
        "10:00",
        "ประชุมสาย",
        note="passed: tomorrow",
    ),
    # finished, lazily
    Case("paper อ่านเสร็จละ", "done", task_no=1),
    Case("เสร็จ 3", "done", task_no=3),
    Case("ส่งรายงานเรียบร้อย", "done", task_no=3),
    Case("เมื่อวาน 9 โมง ประชุม", "unknown", note="a day that is over"),
    # asking, lazily
    Case("วันนี้ต้องทำอะไร", "today"),
    Case("งานค้างทั้งหมด", "all"),
    Case("ใช้ยังไง", "help"),
    # noise: must never become a task
    Case("วันนี้ 9 โมง ประชุม", "add", "2026-10-29", "09:00", "ประชุม", note="9:00 passed at 14:00"),
    Case("👍🏻", "unknown"),
    Case("เลื่อนส่งรายงานเป็นเมื่อวาน", "unknown", note="never move into the past"),
    Case("วันศุกร์", "unknown", note="a day with no task"),
    # change and delete: the task comes from the open list
    Case("ไม่ทำงาน 3 แล้ว ลบทิ้ง", "delete", task_no=3),
    Case("ลบอ่าน paper ออก", "delete", task_no=1),
    Case("ลบงาน 9", "unknown", note="#9 is not this user's open task"),
    Case("เลื่อนงานซักผ้าเป็นเสาร์", "unknown", note="no open task called that"),
    Case("ย้าย paper ไปวันจันทร์หน้า", "update", "2026-11-02", None, task_no=1),
    Case(
        "ส่งรายงานเลื่อนไปวันอาทิตย์",
        "update",
        "2026-11-01",
        "14:00",
        task_no=3,
        note="keeps 14:00",
    ),
    Case(
        "เลื่อนส่งรายงานออกไป 3 วัน",
        "update",
        "2026-11-02",
        "14:00",
        task_no=3,
        note="due 10-30 + 3",
    ),
    Case("แก้ชื่อ paper เป็น paper บทที่ 5", "update", None, None, "บทที่ 5", task_no=1),
    # several tasks in one message
    Case(
        "ซื้อไข่, จ่ายค่าน้ำ, ล้างแอร์ พรุ่งนี้",
        "add",
        note="one day for all three",
        items=(
            Case("", "add", "2026-10-29", None, "ไข่"),
            Case("", "add", "2026-10-29", None, "ค่าน้ำ"),
            Case("", "add", "2026-10-29", None, "แอร์"),
        ),
    ),
    Case(
        "พรุ่ง 8โมง ฟิตเนส บ่ายสอง ประชุม",
        "add",
        items=(
            Case("", "add", "2026-10-29", "08:00", "ฟิตเนส"),
            Case("", "add", "2026-10-29", "14:00", "ประชุม"),
        ),
    ),
    Case(
        "ส่งแบบ 20พย แล้ว ส่งงวดสอง 5ธค",
        "add",
        items=(
            Case("", "add", "2026-11-20", None, "ส่งแบบ"),
            Case("", "add", "2026-12-05", None, "งวดสอง"),
        ),
    ),
    Case(
        "1) ซื้อกาแฟ\n2) โทรหาช่างศุกร์นี้\n3) เติมน้ำมันพรุ่งนี้บ่ายสี่",
        "add",
        items=(
            Case("", "add", None, None, "กาแฟ"),
            Case("", "add", "2026-10-30", None, "ช่าง"),
            Case("", "add", "2026-10-29", "16:00", "น้ำมัน"),
        ),
    ),
    Case(
        "paper เสร็จแล้ว พรุ่งนี้พาแมวไปหาหมอ",
        "add",
        note="done + add",
        items=(Case("", "done", task_no=1), Case("", "add", "2026-10-29", None, "แมว")),
    ),
    Case(
        "เลื่อนส่งรายงานไปศุกร์หน้า ซื้อผงซักฟอก",
        "add",
        note="update + add",
        items=(
            Case("", "update", "2026-11-06", "14:00", task_no=3),
            Case("", "add", None, None, "ผงซักฟอก"),
        ),
    ),
    Case(
        "จ่ายค่าโทรศัพท์ พรุ่งนี้, ทำบัตรประชาชน ศุกร์",
        "add",
        items=(
            Case("", "add", "2026-10-29", None, "ค่าโทรศัพท์"),
            Case("", "add", "2026-10-30", None, "บัตร"),
        ),
    ),
    Case("ซื้อผัก หมู ปลา", "add", None, None, "ซื้อ", note="one errand = one task"),
    Case(
        "จบทั้ง 1 และ 3 แล้ว",
        "done",
        items=(Case("", "done", task_no=1), Case("", "done", task_no=3)),
    ),
    Case(
        "จ่ายค่าส่วนกลาง 2ธค ส่งแบบประเมิน 5ธค ต่ออายุโดเมน 9ธค",
        "add",
        items=(
            Case("", "add", "2026-12-02", None, "ค่าส่วนกลาง"),
            Case("", "add", "2026-12-05", None, "แบบประเมิน"),
            Case("", "add", "2026-12-09", None, "โดเมน"),
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
    if case.action in ("add", "update"):
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
