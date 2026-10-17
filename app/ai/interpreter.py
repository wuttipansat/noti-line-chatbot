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


def _parse_due(
    intent: AiIntent, context: ExtractContext
) -> tuple[date | None, datetime | None]:
    if intent.due_date is None:
        if intent.due_time is not None:
            raise Rejected("due_time without due_date")
        return None, None

    try:
        day = date.fromisoformat(intent.due_date)
    except ValueError as exc:
        raise Rejected(f"not a real date: {intent.due_date!r}") from exc
    if day < context.today:
        raise Rejected("deadline in the past")
    if day > context.today + timedelta(days=MAX_DAYS_AHEAD):
        raise Rejected("deadline too far ahead")
    if intent.due_time is None:
        return day, None
    local = datetime.combine(
        day, time.fromisoformat(intent.due_time), ZoneInfo(context.timezone)
    )
    return None, local


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
            return Command(intent.action)


async def interpret(
    text: str, context: ExtractContext, provider: LLMProvider
) -> Command | None:
    """Ask the provider, then validate. None = could not get a safe answer."""
    try:
        raw = await provider.extract(text, context)
    except Exception:
        logger.exception("provider %s failed", provider.name)
        return None
    try:
        return validate(raw, context)
    except Rejected as exc:
        logger.warning("provider %s answer rejected: %s", provider.name, exc)
        return None
