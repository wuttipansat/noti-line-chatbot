"""Flex Message cards, drawn as pages of a Thai tear-off desk calendar (ปฏิทินฉีก).

A page is white paper hanging from a coloured binding strip, with the date printed
large in the Thai colour of its weekday, and a perforated line where it tears off.
Below the perforation the page is an agenda: time, task, and a box to tick.

Pure functions that build LINE Flex JSON. Paste the output of
`python -m scripts.flex_preview` into the LINE Flex Message Simulator to see it.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.bot.mascot import Mood, robot

# --- Paper and ink ---
PAPER = "#FFFFFF"
INK = "#1F2329"  # titles
INK_SOFT = "#5F6670"  # times, numbers, secondary text (AA on white)
RULE = "#E4E6EA"  # hairlines between rows
PERFORATION = "#C9CDD3"  # the tear-off holes
LATE = "#B42318"  # overdue (AA on white)


@dataclass(frozen=True)
class DayColour:
    """A Thai weekday colour in two strengths."""

    fill: str  # bright: binding strip, markers (never behind small text)
    ink: str  # deep: text printed on white paper (AA for large text and up)


# Monday first, matching date.weekday().
DAY_COLOURS = [
    DayColour("#F4C21B", "#8A6100"),  # จันทร์ เหลือง
    DayColour("#E8559A", "#B0326E"),  # อังคาร ชมพู
    DayColour("#2BA866", "#17784A"),  # พุธ เขียว
    DayColour("#F07F1F", "#B0510A"),  # พฤหัสบดี ส้ม
    DayColour("#3B8FE0", "#1F5FA8"),  # ศุกร์ ฟ้า
    DayColour("#8357D6", "#5E36A8"),  # เสาร์ ม่วง
    DayColour("#E5484D", "#B42328"),  # อาทิตย์ แดง
]
THAI_DAY_NAMES = ["วันจันทร์", "วันอังคาร", "วันพุธ", "วันพฤหัสบดี", "วันศุกร์", "วันเสาร์", "วันอาทิตย์"]
THAI_MONTH_NAMES = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
                    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]  # fmt: skip

MAX_ROWS = 12  # keep one bubble small (LINE limits the JSON size)
ALT_TEXT_LIMIT = 400
PERFORATION_HOLES = 22


def day_colour(day: date) -> DayColour:
    return DAY_COLOURS[day.weekday()]


def thai_month_year(day: date) -> str:
    return f"{THAI_MONTH_NAMES[day.month - 1]} {day.year + 543}"  # Buddhist year


@dataclass(frozen=True)
class Row:
    """One task line: a time column, the title, and a box to tick."""

    number: int
    when: str  # "10:00", "ทั้งวัน", or the due date when overdue
    title: str
    important: bool = False
    overdue: bool = False


@dataclass(frozen=True)
class Section:
    label: str  # "เลยกำหนด", "จ. 2 พ.ย.", or "" for no heading
    rows: list[Row]
    marker: str | None = None  # weekday colour square before the label
    note: str = ""  # right side of the heading, e.g. "พรุ่งนี้"
    late: bool = False


def _text(text: str, **style: Any) -> dict[str, Any]:
    return {"type": "text", "text": text, **style}


def _box(layout: str, contents: list[dict[str, Any]], **style: Any) -> dict[str, Any]:
    return {"type": "box", "layout": layout, "contents": contents, **style}


def _binding(colours: list[str]) -> dict[str, Any]:
    """The strip the pages hang from. One colour, or seven for a week."""
    bars = [_box("vertical", [], flex=1, backgroundColor=c) for c in colours]
    return _box("horizontal", bars, height="8px")


def _perforation() -> dict[str, Any]:
    """A row of small holes: the line where today's page tears off."""
    hole = _box(
        "vertical",
        [],
        width="4px",
        height="4px",
        cornerRadius="2px",
        backgroundColor=PERFORATION,
    )
    return _box(
        "horizontal",
        [hole] * PERFORATION_HOLES,
        justifyContent="space-between",
        paddingStart="12px",
        paddingEnd="12px",
    )


def calendar_page(
    day: date, greeting: str | None = None, mood: Mood = "normal"
) -> dict[str, Any]:
    """Header of a day card: binding, the printed date and the robot, then the perforation."""
    colour = day_colour(day)
    side = [
        _text(
            THAI_DAY_NAMES[day.weekday()], size="lg", weight="bold", color=colour.ink
        ),
        _text(thai_month_year(day), size="sm", color=INK_SOFT),
    ]
    if greeting:
        side.append(_text(greeting, size="xs", color=INK, wrap=True, margin="md"))
    printed = _box(
        "horizontal",
        [
            _text(str(day.day), size="5xl", weight="bold", color=colour.ink, flex=0),
            _box(
                "vertical", side, flex=1, justifyContent="center", paddingStart="14px"
            ),
            robot(mood, colour.fill),
        ],
        alignItems="center",
        paddingTop="12px",
        paddingBottom="14px",
        paddingStart="20px",
        paddingEnd="20px",
    )
    return _box(
        "vertical",
        [_binding([colour.fill]), printed, _perforation()],
        paddingAll="0px",
        backgroundColor=PAPER,
    )


def list_page(
    title: str, subtitle: str, count: str, binding: list[str]
) -> dict[str, Any]:
    """Header of a list card: binding strip, title with the count on the right."""
    heading = _box(
        "horizontal",
        [
            _text(title, size="lg", weight="bold", color=INK, flex=1),
            _text(
                count, size="sm", color=INK_SOFT, flex=0, align="end", gravity="bottom"
            ),
        ],
    )
    text = _box(
        "vertical",
        [heading, _text(subtitle, size="sm", color=INK_SOFT)],
        paddingTop="14px",
        paddingStart="20px",
        paddingEnd="20px",
    )
    return _box("vertical", [_binding(binding), text], paddingAll="0px")


def week_binding(start: date) -> list[str]:
    """Seven weekday colours starting from `start`."""
    return [DAY_COLOURS[(start.weekday() + i) % 7].fill for i in range(7)]


def _tick_box(row: Row) -> dict[str, Any]:
    """A 20px square inside a 36px tap area. Tapping sends "#N เสร็จ"."""
    square = _box(
        "vertical",
        [],
        width="20px",
        height="20px",
        cornerRadius="2px",
        borderWidth="2px",
        borderColor=INK_SOFT,
    )
    return _box(
        "vertical",
        [square],
        flex=0,
        width="36px",
        height="36px",
        justifyContent="center",
        alignItems="center",
        action={
            "type": "message",
            "label": f"เสร็จ #{row.number}",
            "text": f"#{row.number} เสร็จ",
        },
    )


def _task_row(row: Row) -> dict[str, Any]:
    when = _text(
        row.when, size="xs", color=LATE if row.overdue else INK_SOFT, wrap=True
    )
    title = _text(
        row.title,
        size="sm",
        color=INK,
        wrap=True,
        maxLines=2,  # long Thai titles: two lines, then "…"
        weight="bold" if row.important else "regular",
    )
    number = f"#{row.number}" + ("  สำคัญ" if row.important else "")
    return _box(
        "horizontal",
        [
            _box("vertical", [when], flex=0, width="52px", paddingTop="2px"),
            _box(
                "vertical", [title, _text(number, size="xxs", color=INK_SOFT)], flex=1
            ),
            _tick_box(row),
        ],
        alignItems="center",
        paddingTop="8px",
        paddingBottom="8px",
    )


def _section_heading(section: Section) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    if section.marker:
        parts.append(
            _box(
                "vertical",
                [],
                flex=0,
                width="10px",
                height="10px",
                backgroundColor=section.marker,
            )
        )
    colour = LATE if section.late else INK
    parts.append(_text(section.label, size="sm", weight="bold", color=colour, flex=1))
    if section.note:
        parts.append(
            _text(section.note, size="xs", color=INK_SOFT, flex=0, align="end")
        )
    return _box(
        "horizontal",
        parts,
        spacing="sm",
        alignItems="center",
        paddingTop="14px",
        paddingBottom="6px",
    )


def task_card(
    header: dict[str, Any],
    sections: list[Section],
    alt_text: str,
    buttons: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A page listing tasks, agenda style. Rows past MAX_ROWS become "และอีก N งาน"."""
    body: list[dict[str, Any]] = []
    shown = hidden = 0
    for section in sections:
        rows = section.rows[: max(0, MAX_ROWS - shown)]
        hidden += len(section.rows) - len(rows)
        if not rows:
            continue
        if section.label:
            body.append(_section_heading(section))
        for i, row in enumerate(rows):
            if i or not section.label:
                body.append({"type": "separator", "color": RULE})
            body.append(_task_row(row))
        shown += len(rows)
    if hidden:
        body.append({"type": "separator", "color": RULE})
        body.append(
            _text(f"และอีก {hidden} งาน", size="xs", color=INK_SOFT, margin="md")
        )

    message: dict[str, Any] = {
        "type": "flex",
        "altText": alt_text[:ALT_TEXT_LIMIT],  # notification / chat-list preview
        "contents": {
            "type": "bubble",
            "size": "mega",
            "header": header,
            "body": _box(
                "vertical",
                body,
                paddingStart="20px",
                paddingEnd="12px",
                paddingTop="0px",
                paddingBottom="16px",
            ),
        },
    }
    if buttons:
        message["quickReply"] = {"items": buttons}
    return message


def note_card(
    header: dict[str, Any], alt_text: str, buttons: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """A page with nothing listed under it (the header says it all)."""
    message: dict[str, Any] = {
        "type": "flex",
        "altText": alt_text[:ALT_TEXT_LIMIT],
        "contents": {"type": "bubble", "size": "mega", "header": header},
    }
    if buttons:
        message["quickReply"] = {"items": buttons}
    return message
