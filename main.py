import asyncio

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse
from prometheus_fastapi_instrumentator import Instrumentator

PING_HOST = "77.88.8.8"
PING_TIMEOUT = 5  # seconds

app = FastAPI()
Instrumentator().instrument(app).expose(app)


@app.post("/", response_class=PlainTextResponse)
async def hello(test: str | None = Header(default=None)) -> str:
    if test != "Hello":
        raise HTTPException(status_code=403, detail="Forbidden")
    return "Hello, World!"


async def ping(host: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", host,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return False
    try:
        return await asyncio.wait_for(proc.wait(), timeout=PING_TIMEOUT) == 0
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False


@app.get("/health", response_class=PlainTextResponse)
async def health() -> str:
    if not await ping(PING_HOST):
        raise HTTPException(status_code=503, detail=f"{PING_HOST} is unreachable")
    return "OK"
