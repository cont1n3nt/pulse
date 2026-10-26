import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend.agent import stream_message

app = FastAPI(title="Pulse API", version="1.0.0")
FRONTEND_INDEX = Path(__file__).resolve().parent.parent / "frontend" / "index.html"
FRONTEND_DIR = FRONTEND_INDEX.parent

app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")


class ChatMessage(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    session_id: str = Field(default="default", min_length=1, max_length=100)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND_INDEX, media_type="text/html")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/messages", tags=["chat"])
def receive_message(message: ChatMessage) -> StreamingResponse:
    text = message.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Текст сообщения не может быть пустым")

    def events():
        for event in stream_message(message.session_id, text):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
