"""A pretend LINE Messaging API for local testing. Prints what the bot sends.

Run:   uvicorn scripts.fake_line_api:app --port 9000
Then set in .env:  LINE_API_BASE_URL=http://127.0.0.1:9000
"""

from fastapi import FastAPI, Request

app = FastAPI(title="fake LINE API")


def show(kind: str, body: dict) -> None:
    for message in body.get("messages", []):
        print(f"[{kind}] bot says: {message.get('text')}", flush=True)


@app.post("/v2/bot/message/reply")
async def reply(request: Request) -> dict:
    show("reply", await request.json())
    return {}


@app.post("/v2/bot/message/push")
async def push(request: Request) -> dict:
    body = await request.json()
    print(f"[push] retry key {request.headers.get('x-line-retry-key')}", flush=True)
    show("push", body)
    return {}
