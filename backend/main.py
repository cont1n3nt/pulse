from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

app = FastAPI(title="Pulse API", version="1.0.0")
FRONTEND_INDEX = Path(__file__).resolve().parent.parent / "frontend" / "index.html"
FRONTEND_DIR = FRONTEND_INDEX.parent

app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")


class ChatMessage(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND_INDEX, media_type="text/html")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/messages", tags=["chat"])
def receive_message(message: ChatMessage) -> dict[str, str]:
    text = message.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Текст сообщения не может быть пустым")
    return {"text": text, "status": "received"}
