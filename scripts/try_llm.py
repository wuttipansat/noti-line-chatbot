"""Send one sentence to the configured model and show what it returns.

Run:  python -m scripts.try_llm "พรุ่งนี้บ่ายสองส่งรายงาน"
"""

import asyncio
import sys
from datetime import UTC, datetime

from app.ai.factory import build_provider
from app.ai.interpreter import Rejected, validate
from app.ai.provider import ExtractContext, OpenTask
from app.config import get_settings
from app.services.dates import local_today


async def main(text: str) -> None:
    settings = get_settings()
    provider = build_provider(settings)
    context = ExtractContext(
        today=local_today(datetime.now(UTC), settings.default_timezone),
        timezone=settings.default_timezone,
        open_tasks=[OpenTask(1, "อ่าน paper"), OpenTask(2, "ส่งรายงาน")],
    )
    print(f"provider : {provider.name}")
    raw = await provider.extract(text, context)
    print(f"raw      : {raw}")
    try:
        print(f"command  : {validate(raw, context)}")
    except Rejected as exc:
        print(f"REJECTED : {exc}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "พรุ่งนี้บ่ายสองส่งรายงาน"))
