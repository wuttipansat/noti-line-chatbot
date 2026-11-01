"""A rule-based stand-in for a language model. No network, no cost, predictable.

Used in tests, in local development as the fallback when the
real model is down. It only understands "a task with a date or time":
    "พรุ่งนี้บ่ายสองส่งรายงาน", "ประชุมทีม 20/10/69 09:30", "วันนี้ 17:00 โทรหาลูกค้า"
An ambiguous hour comes back with time_options to ask about.
Everything else -> {"action": "unknown"}.
"""

import json
import re
from datetime import date, timedelta

from app.ai.provider import ExtractContext
from app.services.dates import DateParseError, end_of_month, parse_date, weekday_date

THAI_NUMBERS = {"หนึ่ง": 1, "สอง": 2, "สาม": 3, "สี่": 4, "ห้า": 5, "หก": 6}
WEEKDAYS = {"จันทร์": 0, "อังคาร": 1, "พุธ": 2, "พฤหัส": 3, "พฤหัสบดี": 3,
            "ศุกร์": 4, "เสาร์": 5, "อาทิตย์": 6}  # fmt: skip
WEEKDAY = re.compile(
    r"(?:วัน)?(พฤหัสบดี|พฤหัส|จันทร์|อังคาร|พุธ|ศุกร์|เสาร์|อาทิตย์)"
    r"\s*(สัปดาห์หน้า|อาทิตย์หน้า|หน้า|นี้)?"
)
IN_DAYS = re.compile(r"อีก\s*(\d{1,3})\s*วัน")
MONTH_END = re.compile(r"สิ้นเดือน(?:นี้)?")

DAY_WORD = re.compile(r"มะรืนนี้|มะรืน|พรุ่งนี้|วันนี้")
DAY_DATE = re.compile(
    r"\d{1,2}/\d{1,2}/\d{2,4}"
    r"|\d{1,2}\s*(?:ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.)"
    r"(?:\s*\d{2,4})?"
)
HALF = r"(\s*ครึ่ง)?"
NUM = r"(\d|หนึ่ง|สอง|สาม|สี่|ห้า)"
EVENING = re.compile(rf"(?:{NUM}\s*ทุ่ม|ทุ่ม\s*(?:นึง|หนึ่ง)){HALF}")  # 19:00-23:00
NIGHT = re.compile(rf"ตี\s*{NUM}{HALF}")  # 01:00-05:00
CLOCK = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b")
AFTERNOON = re.compile(rf"บ่าย\s*{NUM}?(?:\s*โมง)?{HALF}")  # "บ่ายโมง" = 13:00
NOON = re.compile(rf"เที่ยง(?:วัน)?(?!คืน){HALF}")
EVENING_HOUR = re.compile(r"(?<!\d)([1-6]|หนึ่ง|สอง|สาม|สี่|ห้า|หก)\s*โมงเย็น")
AFTERNOON_HOUR = re.compile(
    rf"(?<!\d)([1-5]|หนึ่ง|สอง|สาม|สี่|ห้า)\s*โมง(?!\s*(?:เช้า|เย็น)){HALF}"
)
# "หกโมง" alone is really ambiguous: 06:00 or 18:00 -> ask.
AMBIGUOUS = re.compile(r"(?<!\d)(?:6|หก)\s*โมง(?!\s*(?:เช้า|เย็น|ครึ่ง))")
MORNING_HOUR = re.compile(
    rf"(?<!\d)(7|8|9|10|11|เจ็ด|แปด|เก้า|สิบเอ็ด|สิบ)\s*โมง(?:\s*เช้า)?{HALF}"
)
SIX_AM = re.compile(rf"(?<!\d)(?:6|หก)\s*โมงเช้า{HALF}")
MORNING_NUMBERS = {"เจ็ด": 7, "แปด": 8, "เก้า": 9, "สิบ": 10, "สิบเอ็ด": 11}
QUESTION = re.compile(r"อะไร|ไหม|มั้ย|บ้าง|หรือเปล่า|\?")  # asking, not telling
URGENT = re.compile(r"\s*ด่วน(?:มาก|ที่สุด)?")
FILLER = re.compile(r"^(?:เตือน(?:ให้)?|ต้อง|ให้)\s*|\s*(?:ด้วย|หน่อย|นะ|ครับ|ค่ะ)$")


def _number(word: str) -> int:
    return THAI_NUMBERS.get(word) or int(word)


def _clock(hour: int, half: str | None) -> str:
    return f"{hour % 24:02d}:{'30' if half else '00'}"


class MockProvider:
    name = "mock"

    async def extract(self, text: str, context: ExtractContext) -> str:
        return json.dumps(self._parse(text, context), ensure_ascii=False)

    @staticmethod
    def _day(text: str, today: date) -> tuple[date | None, str]:
        """Find one day phrase. Returns (day or None, the text without it)."""
        if match := IN_DAYS.search(text):
            day = today + timedelta(days=int(match[1]))
        elif match := MONTH_END.search(text):
            day = end_of_month(today)
        elif match := WEEKDAY.search(text):
            day = weekday_date(WEEKDAYS[match[1]], today, match[2] not in (None, "นี้"))
        elif match := DAY_WORD.search(text) or DAY_DATE.search(text):
            day = parse_date(match.group(0), today)
        else:
            return None, text
        return day, text.replace(match.group(0), " ")

    def _parse(self, text: str, context: ExtractContext) -> dict:
        rest = " ".join(text.split())
        try:
            day, rest = self._day(rest, context.today)
        except DateParseError:
            return {"action": "unknown"}

        clock = None
        options = None
        if match := CLOCK.search(rest):
            clock = f"{int(match[1]):02d}:{match[2]}"
        elif match := AFTERNOON.search(rest):
            clock = _clock(12 + _number(match[1] or "1"), match[2])
        elif match := NOON.search(rest):
            clock = _clock(12, match[1])
        elif match := EVENING.search(rest):
            clock = _clock(18 + _number(match[1] or "1"), match[2])
        elif match := NIGHT.search(rest):
            clock = _clock(_number(match[1]), match[2])
        elif match := EVENING_HOUR.search(rest):
            clock = _clock(12 + _number(match[1]), None)
        elif match := SIX_AM.search(rest):
            clock = _clock(6, match[1])
        elif match := MORNING_HOUR.search(rest):
            clock = _clock(MORNING_NUMBERS.get(match[1]) or int(match[1]), match[2])
        elif match := AFTERNOON_HOUR.search(rest):
            clock = _clock(12 + _number(match[1]), match[2])
        elif match := AMBIGUOUS.search(rest):
            options = ["06:00", "18:00"]
        if match:
            rest = rest.replace(match.group(0), " ")

        if day is None and clock is None and options is None:
            return {"action": "unknown"}
        title = " ".join(rest.split())
        for _ in range(3):  # peel several filler words: "เตือนให้ ... ด้วยนะ"
            title = FILLER.sub("", title).strip()
        if not title or QUESTION.search(title):
            return {"action": "unknown"}
        priority = 3 if URGENT.search(title) else 2
        title = URGENT.sub("", title).strip()
        answer = {
            "action": "add",
            "title": title,
            "due_date": day.isoformat() if day else None,  # no day: interpreter decides
            "due_time": clock,
        }
        if priority != 2:
            answer["priority"] = priority
        if options:
            answer["time_options"] = options
        return answer
