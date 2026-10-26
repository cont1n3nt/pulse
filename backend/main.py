from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

app = FastAPI(title="Pulse API", version="1.0.0")
FRONTEND_INDEX = Path(__file__).resolve().parent.parent / "frontend" / "index.html"
FRONTEND_DIR = FRONTEND_INDEX.parent

app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")


class Message(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    text: str = Field(min_length=1, max_length=5000)


class ChatRequest(BaseModel):
    chat_id: str = Field(min_length=1, max_length=100)
    messages: list[Message] = Field(min_length=1, max_length=500)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND_INDEX, media_type="text/html")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/messages", tags=["chat"])
def receive_message(request: ChatRequest) -> dict[str, str]:
    latest_user_message = next(
        (message.text for message in reversed(request.messages) if message.role == "user"),
        None,
    )
    if latest_user_message is None or not latest_user_message.strip():
        raise HTTPException(status_code=422, detail="Текст сообщения не может быть пустым")
    return {"chat_id": request.chat_id, "text": latest_user_message}
