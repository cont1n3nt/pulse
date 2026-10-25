"""
    uv add fastapi uvicorn
    uv run uvicorn api_example:app --port 8080
"""

from fastapi import FastAPI

from agent import AgentResponse, ChatRequest, TripState, get_state, handle_message

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/chat", response_model=AgentResponse)
def chat(req: ChatRequest):          # sync def → FastAPI сам выполнит в пуле потоков
    return handle_message(req.session_id, req.message)


@app.get("/api/trip/{session_id}", response_model=TripState)
def trip(session_id: str):
    return get_state(session_id)
