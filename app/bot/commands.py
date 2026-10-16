"""Understand a text message without AI: a small set of fixed commands.
"""

import re
from dataclasses import dataclass
from typing import Literal

Action = Literal["help", "add", "today", "week", "all", "done", "unknown"]


@dataclass(frozen=True)
class Command:
    action: Action
    title: str | None = None
    task_no: int | None = None


HELP_WORDS = {"help", "ช่วยเหลือ", "วิธีใช้", "เมนู"}
TODAY_WORDS = {"งานวันนี้", "วันนี้", "วันนี้มีงานอะไร"}
WEEK_WORDS = {"งาน 7 วัน", "งาน7วัน", "งานสัปดาห์นี้"}
ALL_WORDS = {"งานทั้งหมด", "ดูงานทั้งหมด"}

ADD = re.compile(r"^(?:เพิ่มงาน|เพิ่ม|add)\s*(?P<title>.*)$", re.IGNORECASE)
DONE = re.compile(
    r"^(?:#(?P<a>\d+)\s*(?:เสร็จแล้ว|เสร็จ|done)"
    r"|(?:เสร็จ|done)\s*#?(?P<b>\d+))$",
    re.IGNORECASE,
)


def parse_command(text: str) -> Command:
    cleaned = " ".join(text.split())
    lowered = cleaned.lower()

    if lowered in HELP_WORDS:
        return Command("help")
    if cleaned in TODAY_WORDS:
        return Command("today")
    if cleaned in WEEK_WORDS:
        return Command("week")
    if cleaned in ALL_WORDS:
        return Command("all")
    if match := DONE.match(cleaned):
        return Command("done", task_no=int(match["a"] or match["b"]))
    if match := ADD.match(cleaned):
        return Command("add", title=match["title"].strip())
    return Command("unknown")