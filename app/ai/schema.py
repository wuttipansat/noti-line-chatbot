"""JSON schema acceptance from LLM.

extra='forbid': unknown fields are an error, not ignored.
Literal action: anything outside the allowlist is an error.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AiAction = Literal["add", "done", "today", "week", "all", "help", "unknown"]


class AiIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: AiAction
    title: str | None = Field(default=None, max_length=200)
    task_no: int | None = Field(default=None, ge=1)
    due_date: str | None = None  # format YYYY-MM-DD in the user's timezone
    due_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]:[0-5]\d$)")
    priority: int = Field(default=2, ge=1, le=3)
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
