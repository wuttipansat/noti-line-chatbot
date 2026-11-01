"""Turn untrusted model output into a trusted Command - or refuse.

Every check here exists because a model can return it:
broken JSON, extra fields, actions never allow, another user's task number,
dates that do not exist, deadlines in the past. Any failure -> None (no change).
"""

import json
import logging
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx2
from pydantic import ValidationError

from app.ai.provider import ExtractContext, LLMProvider
from app.ai.schema import AiIntent
from app.bot.commands import Command

logger = logging.getLogger(__name__)

MAX_DAYS_AHEAD = 366 * 2
CODE_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class Rejected(ValueError):
    """The model's answer broke a rule. The message says which (for logs only)."""


def _strip_code_fence(raw: str) -> str:
    raw = raw.strip()
    match = CODE_FENCE.match(raw)
    return match.group(1) if match else raw


def _parse_day(value: str, context: ExtractContext) -> date:
    try:
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise Rejected(f"not a real date: {value!r}") from exc
    if day < context.today:
        raise Rejected("deadline in the past")
    if day > context.today + timedelta(days=MAX_DAYS_AHEAD):
        raise Rejected("deadline too far ahead")
    return day


def _day_for_time_only(due_time: str, context: ExtractContext) -> date:
    """ "บ่ายสามโทรหาลูกค้า": today, or tomorrow if that time has already passed."""
    if context.now is not None and time.fromisoformat(due_time) <= context.now:
        return context.today + timedelta(days=1)
    return context.today


def _parse_due(
    intent: AiIntent, context: ExtractContext
) -> tuple[date | None, datetime | None]:
    if intent.due_date is None:
        if intent.due_time is None:
            return None, None
        day = _day_for_time_only(intent.due_time, context)
    else:
        day = _parse_day(intent.due_date, context)
    if intent.due_time is None:
        return day, None
    local = datetime.combine(
        day, time.fromisoformat(intent.due_time), ZoneInfo(context.timezone)
    )
    return None, local


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


def validate(raw: str, context: ExtractContext) -> Command:
    """Check the model's JSON against every rule. Raises Rejected."""
    try:
        intent = AiIntent.model_validate_json(_strip_code_fence(raw))

    except (ValidationError, json.JSONDecodeError) as exc:
        raise Rejected("answer is not valid JSON of the expected schema") from exc

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


async def interpret_explained(
    text: str, context: ExtractContext, provider: LLMProvider
) -> tuple[Command | None, str]:
    """Like interpret(), plus why: "ok", or what went wrong (for logs and evals)."""
    try:
        raw = await provider.extract(text, context)
    except (httpx2.HTTPStatusError, httpx2.TransportError) as exc:
        # Busy, quota or network: expected now and then, one line is enough.
        logger.warning("provider %s unavailable: %s", provider.name, _why(exc))
        return None, f"provider failed: {_why(exc)}"
    except Exception as exc:  # anything else is a bug worth a full traceback
        logger.exception("provider %s failed", provider.name)
        return None, f"provider failed: {type(exc).__name__}"
    try:
        return validate(raw, context), "ok"
    except Rejected as exc:
        logger.warning("provider %s answer rejected: %s", provider.name, exc)
        return None, f"rejected: {exc}"


def _why(exc: Exception) -> str:
    if isinstance(exc, httpx2.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


async def interpret(
    text: str, context: ExtractContext, provider: LLMProvider
) -> Command | None:
    """Ask the provider, then validate. None = could not get a safe answer."""
    command, _ = await interpret_explained(text, context, provider)
    return command
