"""The contract every LLM providers must follow.

A provider turns the user's text into a JSON string. That string is UNTRUSTED:
app/ai/interpreter.py checks every field before anything touches the database.
"""

from dataclasses import dataclass
from datetime import date, time
from typing import Protocol


@dataclass(frozen=True)
class OpenTask:
    task_no: int
    title: str
    due_date: date | None = None  # local day, when the task has a deadline
    due_time: time | None = None  # local clock time, when it has one


@dataclass(frozen=True)
class ExtractContext:
    """What the model may know: today's date, the timezone, and This user's open tasks."""

    today: date
    timezone: str
    open_tasks: list[OpenTask]
    now: time | None = None  # the user's local clock time, when we know it


class LLMProvider(Protocol):
    name: str

    async def extract(self, text: str, context: ExtractContext) -> str:
        """Return a JSON object as text (see app/ai/schema.py for the schema)."""
        ...
