"""A rule-based stand-in for a language model. No network, no cost, predictable.

Used in tests, in local development as the fallback when the real model is down. It only understands "a task with a date or time":
    "พรุ่งนี้บ่ายสองส่งรายงาน", "ประชุมทีม 20/10/69 09:30", "วันนี้ 17:00 โทรหาลูกค้า"
Everything else -> {"action": "unknown"}.
"""

import json
import re

from app.ai.provider import ExtractContext
from app.services.dates import DateParseError, parse_date

THAI_NUMBERS = {"หนึ่ง": 1, "สอง": 2, "สาม": 3, "สี่": 4, "ห้า": 5}

DAY_WORD = re.compile(r"มะรืนนี้|มะรืน|พรุ่งนี้|วันนี้")
DAY_DATE = re.compile(
    r"\d{1,2}/\d{1,2}/\d{2,4}"
    r"|\d{1,2}\s*(?:ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.)"
    r"(?:\s*\d{2,4})?"
)
CLOCK = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b")
AFTERNOON = re.compile(r"บ่าย\s*(\d|หนึ่ง|สอง|สาม|สี่|ห้า)(?:\s*โมง)?")
NOON = re.compile(r"เที่ยง(?:วัน)?")
FILLER = re.compile(r"^(?:เตือน(?:ให้)?|ต้อง|ให้)\s*|\s*(?:ด้วย|หน่อย|นะ|ครับ|ค่ะ)$")


class MockProvider:
    name = "mock"

    async def extract(self, text: str, context: ExtractContext) -> str:
        return json.dumps(self._parse(text, context), ensure_ascii=False)

    def _parse(self, text: str, context: ExtractContext) -> dict:
        rest = " ".join(text.split())
        day = None
        if match := DAY_WORD.search(rest) or DAY_DATE.search(rest):
            try:
                day = parse_date(match.group(0), context.today)
            except DateParseError:
                return {"action": "unknown"}
            rest = rest.replace(match.group(0), " ")

        clock = None
        if match := CLOCK.search(rest):
            clock = f"{int(match[1]):02d}:{match[2]}"
        elif match := AFTERNOON.search(rest):
            hour = THAI_NUMBERS.get(match[1]) or int(match[1])
            clock = f"{12 + hour:02d}:00"
        elif match := NOON.search(rest):
            clock = "12:00"
        if match:
            rest = rest.replace(match.group(0), " ")

        if day is None and clock is None:
            return {"action": "unknown"}
        title = " ".join(rest.split())
        for _ in range(3):
            title = FILLER.sub("", title).strip()
        if not title:
            return {"action": "unknown"}
        return {
            "action": "add",
            "title": title,
            "due_date": (day or context.today).isoformat(),
            "due_time": clock,
        }
