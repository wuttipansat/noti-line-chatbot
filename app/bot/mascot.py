"""The AI Planner robot, drawn with Flex boxes (no image file, no hosting needed).

Same shape as the app icon: antenna cap, head with ears, a screen with two eyes,
and a chin. The body takes the weekday colour; the face is a dark screen whose
eyes change with the mood.
"""

from typing import Any, Literal

Mood = Literal["normal", "happy", "sleepy", "worried", "wink"]
MOODS: tuple[Mood, ...] = ("normal", "happy", "sleepy", "worried", "wink")

SCREEN = "#1F2329"
EYE = "#FFFFFF"


def _box(layout: str, contents: list[dict[str, Any]], **style: Any) -> dict[str, Any]:
    return {"type": "box", "layout": layout, "contents": contents, **style}


def _shape(width: int, height: int, colour: str, radius: int, **style: Any) -> dict:
    return _box(
        "vertical",
        [],
        width=f"{width}px",
        height=f"{height}px",
        backgroundColor=colour,
        cornerRadius=f"{radius}px",
        flex=0,
        **style,
    )


def _eye(kind: str) -> dict[str, Any]:
    """One eye. Every kind sits in the same 12x14 slot, so the face never shifts."""
    if kind == "open":  # a tall pill, like the icon
        inner = _shape(6, 13, EYE, 3)
    elif kind == "closed":  # a flat line
        inner = _shape(9, 3, EYE, 2)
    elif kind == "ring":  # wide open
        inner = _box(
            "vertical",
            [],
            width="10px",
            height="10px",
            cornerRadius="5px",
            borderWidth="2px",
            borderColor=EYE,
            flex=0,
        )
    else:  # "smile": an upward arch
        inner = {
            "type": "text",
            "text": "^",
            "size": "lg",
            "weight": "bold",
            "color": EYE,
            "align": "center",
            "flex": 0,
        }
    return _box(
        "vertical",
        [inner],
        width="12px",
        height="14px",
        flex=0,
        justifyContent="center",
        alignItems="center",
    )


EYES: dict[Mood, tuple[str, str]] = {
    "normal": ("open", "open"),
    "happy": ("smile", "smile"),
    "sleepy": ("closed", "closed"),
    "worried": ("ring", "ring"),
    "wink": ("open", "closed"),
}


def robot(mood: Mood, body: str) -> dict[str, Any]:
    """The robot, about 64x60 px, in `body` colour with the eyes of `mood`."""
    left, right = EYES[mood]
    eyes = _box(
        "horizontal",
        [_eye(left), _eye(right)],
        spacing="8px",
        justifyContent="center",
        alignItems="center",
    )
    screen = _box(
        "vertical",
        [eyes],
        width="40px",
        height="22px",
        cornerRadius="7px",
        backgroundColor=SCREEN,
        justifyContent="center",
        flex=0,
    )
    head = _box(
        "vertical",
        [screen],
        width="52px",
        height="38px",
        cornerRadius="11px",
        backgroundColor=body,
        justifyContent="center",
        alignItems="center",
        flex=0,
    )
    ear = _shape(5, 16, body, 2)
    face_row = _box(
        "horizontal", [ear, head, ear], spacing="2px", alignItems="center", flex=0
    )
    return _box(
        "vertical",
        [_shape(16, 7, body, 3), face_row, _shape(20, 6, body, 3)],
        spacing="3px",
        alignItems="center",
        flex=0,
    )
