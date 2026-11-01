"""JSON schema acceptance from LLM.

extra='forbid': unknown fields are an error, not ignored.
Literal action: anything outside the allowlist is an error.
"""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"
LOOSE_TIME = re.compile(r"^(\d{1,2})[:.](\d{2})$")


def _pad_time(value):
    """Models sometimes write "9:00" or "9.30": make it "09:00" / "09:30"."""
    if isinstance(value, str) and (match := LOOSE_TIME.match(value.strip())):
        return f"{int(match[1]):02d}:{match[2]}"
    return value


Time = Annotated[str, BeforeValidator(_pad_time), Field(pattern=HHMM)]

AiAction = Literal["add", "done", "today", "week", "all", "help", "unknown"]


class AiIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: AiAction
    title: str | None = Field(default=None, max_length=200)
    task_no: int | None = Field(default=None, ge=1)
    due_date: str | None = None  # format YYYY-MM-DD in the user's timezone
    due_time: Time | None = None
    priority: int = Field(default=2, ge=1, le=3)
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
    time_options: list[Time] = Field(default_factory=list, max_length=3)
