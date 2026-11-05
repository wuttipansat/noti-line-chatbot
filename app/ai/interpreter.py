"""Turn untrusted model output into trusted Commands - or refuse.

Every check here exists because a model can return it:
broken JSON, extra fields, actions never allow, another user's task number,
dates that do not exist, deadlines in the past.

One message can hold several tasks, so the answer is a list. Each item is checked on its
own: a bad item is SKIPPED AND REPORTED (never silently lost), the good ones are kept.
A broken answer as a whole -> no commands (no change).
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx2
from pydantic import ValidationError

from app.ai.provider import ExtractContext, LLMProvider
from app.ai.schema import AiIntent
from app.bot.commands import Command

logger = logging.getLogger(__name__)

MAX_DAYS_AHEAD = 366 * 2
MAX_COMMANDS = 5  # tasks kept from one message
MAX_RAW_COMMANDS = 20  # more than this in one answer = not a believable answer
BATCH_ACTIONS = {
    "add",
    "done",
    "update",
    "ask_time",
}  # the only ones that can be combined
CODE_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class Rejected(ValueError):
    """The model's answer broke a rule. The message says which (for logs only)."""


class PastDeadline(Rejected):
    """The deadline is a day that is already over."""


@dataclass(frozen=True)
class Skipped:
    """Part of the message we did not act on. The user is told about it."""

    label: str | None  # the task's title, when the model gave one
    reason: str  # for logs and evals; never shown to the user
    too_many: bool = False  # True: valid, but over MAX_COMMANDS
    past: bool = False  # True: the deadline is a day that is already over


@dataclass(frozen=True)
class Plan:
    """What to do for one message: commands in order, plus what was skipped."""

    commands: tuple[Command, ...] = ()
    skipped: tuple[Skipped, ...] = ()


def _strip_code_fence(raw: str) -> str:
    raw = raw.strip()
    match = CODE_FENCE.match(raw)
    return match.group(1) if match else raw


def _parse_day(value: str, context: ExtractContext) -> date:
    try:
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise Rejected(f"not a real date: {value!r}") from exc
    return _check_range(day, context)


def _check_range(day: date, context: ExtractContext) -> date:
    if day < context.today:
        raise PastDeadline("deadline in the past")
    if day > context.today + timedelta(days=MAX_DAYS_AHEAD):
        raise Rejected("deadline too far ahead")
    return day


def _not_in_the_past(day: date, clock: time | None, context: ExtractContext) -> date:
    """Today at a time that has already passed means tomorrow."""
    if (
        clock is not None
        and context.now is not None
        and day == context.today
        and clock <= context.now
    ):
        return day + timedelta(days=1)
    return day


def _day_for_time_only(due_time: str, context: ExtractContext) -> date:
    """ "บ่ายสามโทรหาลูกค้า": today, or tomorrow if that time has already passed."""
    return _not_in_the_past(context.today, time.fromisoformat(due_time), context)


def _parse_due(
    intent: AiIntent, context: ExtractContext
) -> tuple[date | None, datetime | None]:
    if intent.due_date is None:
        if intent.due_time is None:
            return None, None
        day = _day_for_time_only(intent.due_time, context)
    else:
        day = _parse_day(intent.due_date, context)
        if intent.due_time is not None:
            day = _not_in_the_past(day, time.fromisoformat(intent.due_time), context)
    if intent.due_time is None:
        return day, None
    local = datetime.combine(
        day, time.fromisoformat(intent.due_time), ZoneInfo(context.timezone)
    )
    return None, local


def _update(intent: AiIntent, context: ExtractContext) -> Command:
    """Change one open task. The program works out the new deadline, never the model."""
    task = next((t for t in context.open_tasks if t.task_no == intent.task_no), None)
    if task is None:
        raise Rejected(f"task #{intent.task_no} is not one of this user's open tasks")
    title = intent.title.strip() if intent.title else None
    changes_day = intent.due_date is not None or intent.shift_days is not None
    if intent.due_date is not None and intent.shift_days is not None:
        raise Rejected("both due_date and shift_days")
    if not title and not changes_day and intent.due_time is None:
        raise Rejected("update changes nothing")
    if not changes_day and intent.due_time is None:
        return Command("update", title=title, task_no=task.task_no)

    if intent.due_date is not None:
        day = _parse_day(intent.due_date, context)
    elif intent.shift_days is not None:
        day = (task.due_date or context.today) + timedelta(days=intent.shift_days)
        _check_range(day, context)
    else:  # only a new time: keep the day (a task with no day: today, or tomorrow)
        day = task.due_date or _day_for_time_only(intent.due_time, context)
    clock = time.fromisoformat(intent.due_time) if intent.due_time else task.due_time
    day = _not_in_the_past(day, clock, context)
    if clock is None:
        return Command("update", title=title, task_no=task.task_no, due_date=day)
    due_at = datetime.combine(day, clock, ZoneInfo(context.timezone))
    return Command("update", title=title, task_no=task.task_no, due_at=due_at)


def _ask_time(intent: AiIntent, context: ExtractContext) -> Command:
    """The time is ambiguous: build a question instead of guessing."""
    options = tuple(sorted(set(intent.time_options)))
    if len(options) < 2:
        raise Rejected("time_options needs at least 2 different times")
    if intent.due_time is not None:
        raise Rejected("both due_time and time_options")
    day = _parse_day(intent.due_date, context) if intent.due_date else context.today
    return Command(
        "ask_time",
        title=intent.title.strip(),
        due_date=day,
        priority=intent.priority,
        estimated_minutes=intent.estimated_minutes,
        time_options=options,
    )


def _command(intent: AiIntent, context: ExtractContext) -> Command:
    """One item of the answer -> a trusted Command. Raises Rejected."""
    match intent.action:
        case "add":
            if not intent.title or not intent.title.strip():
                raise Rejected("add without title")
            if intent.time_options:
                return _ask_time(intent, context)
            due_date, due_at = _parse_due(intent, context)
            return Command(
                "add",
                title=intent.title.strip(),
                due_date=due_date,
                due_at=due_at,
                priority=intent.priority,
                estimated_minutes=intent.estimated_minutes,
            )
        case "update":
            return _update(intent, context)
        case "delete":
            mine = {t.task_no for t in context.open_tasks}
            if intent.task_no not in mine:
                raise Rejected(
                    f"task #{intent.task_no} is not one of this user's open tasks"
                )
            return Command("delete", task_no=intent.task_no)
        case "done":
            mine = {t.task_no for t in context.open_tasks}
            if intent.task_no not in mine:
                raise Rejected(
                    f"task #{intent.task_no} is not one of this user's open tasks"
                )
            return Command("done", task_no=intent.task_no)
        case _:
            if intent.time_options:
                raise Rejected("time_options only make sense with add")
            return Command(intent.action)


def _items(raw: str) -> list:
    """The list of raw commands. A bare single command (no wrapper) is still accepted."""
    try:
        data = json.loads(_strip_code_fence(raw))
    except json.JSONDecodeError as exc:
        raise Rejected("answer is not valid JSON of the expected schema") from exc
    if isinstance(data, dict) and "commands" in data:
        data = data["commands"]
    items = data if isinstance(data, list) else [data]
    if not items:
        raise Rejected("answer has no commands")
    if len(items) > MAX_RAW_COMMANDS:
        raise Rejected(f"answer has {len(items)} commands (limit {MAX_RAW_COMMANDS})")
    return items


def _label(item) -> str | None:
    title = item.get("title") if isinstance(item, dict) else None
    return title.strip()[:60] if isinstance(title, str) and title.strip() else None


def validate_plan(raw: str, context: ExtractContext) -> Plan:
    """Check every item of the model's answer. Raises Rejected only if the whole
    answer is unusable; a bad item is skipped and reported in Plan.skipped."""
    items = _items(raw)
    several = len(items) > 1
    commands: list[Command] = []
    skipped: list[Skipped] = []
    asked_time = False
    for item in items:
        try:
            intent = AiIntent.model_validate(item)
            command = _command(intent, context)
            if several and command.action not in BATCH_ACTIONS:
                raise Rejected(f"{command.action} cannot be combined with other tasks")
        except ValidationError:
            skipped.append(
                Skipped(_label(item), "answer is not valid JSON of the expected schema")
            )
            continue
        except Rejected as exc:
            skipped.append(
                Skipped(_label(item), str(exc), past=isinstance(exc, PastDeadline))
            )
            continue
        if command in commands:
            continue  # the same task twice: keep one
        if command.action == "ask_time":
            if asked_time:  # the bot can wait for only one answer at a time
                skipped.append(Skipped(command.title, "only one time question"))
                continue
            asked_time = True
        if len(commands) == MAX_COMMANDS:
            skipped.append(Skipped(_label(item), "over the limit", too_many=True))
            continue
        commands.append(command)
    return Plan(tuple(commands), tuple(skipped))


def validate(raw: str, context: ExtractContext) -> Command:
    """Exactly one command, or Rejected. (interpret_plan allows several.)"""
    plan = validate_plan(raw, context)
    if len(plan.commands) == 1 and not plan.skipped:
        return plan.commands[0]
    if not plan.commands:
        raise Rejected(plan.skipped[0].reason)
    raise Rejected("answer holds several commands")


async def interpret_plan(
    text: str, context: ExtractContext, provider: LLMProvider
) -> tuple[Plan, str]:
    """Ask the provider, validate every item. Plus why: "ok", or what went wrong."""
    try:
        raw = await provider.extract(text, context)
    except (httpx2.HTTPStatusError, httpx2.TransportError) as exc:
        # Busy, quota or network: expected now and then, one line is enough.
        logger.warning("provider %s unavailable: %s", provider.name, _why(exc))
        return Plan(), f"provider failed: {_why(exc)}"
    except Exception as exc:  # anything else is a bug worth a full traceback
        logger.exception("provider %s failed", provider.name)
        return Plan(), f"provider failed: {type(exc).__name__}"
    try:
        plan = validate_plan(raw, context)
    except Rejected as exc:
        logger.warning("provider %s answer rejected: %s", provider.name, exc)
        return Plan(), f"rejected: {exc}"
    for item in plan.skipped:
        logger.warning("provider %s item skipped: %s", provider.name, item.reason)
    if plan.commands:
        return plan, "ok"
    return plan, f"rejected: {plan.skipped[0].reason}"


async def interpret_explained(
    text: str, context: ExtractContext, provider: LLMProvider
) -> tuple[Command | None, str]:
    """One command (or None) plus why. For messages that hold exactly one task."""
    plan, why = await interpret_plan(text, context, provider)
    if len(plan.commands) == 1 and not plan.skipped:
        return plan.commands[0], why
    return None, why if why != "ok" else "rejected: answer holds several commands"


def _why(exc: Exception) -> str:
    if isinstance(exc, httpx2.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


async def interpret(
    text: str, context: ExtractContext, provider: LLMProvider
) -> Command | None:
    """Ask the provider, then validate. None = could not get a safe single answer."""
    command, _ = await interpret_explained(text, context, provider)
    return command
