const chat = document.querySelector(".chat");
const form = document.querySelector(".chat-form");
const input = form?.querySelector("textarea");
const sendButton = form?.querySelector(".send");

if (chat && form && input && sendButton) {
  input.id = "chat-input";
  input.setAttribute("aria-label", "Текст сообщения");
  input.removeAttribute("readonly");
  sendButton.type = "button";

  const sessionId = crypto.randomUUID();

  const scrollDown = (el) => el.scrollIntoView({ block: "nearest" });

  const addMessage = (text, className) => {
    const bubble = document.createElement("div");
    bubble.className = `bubble ${className}`;
    bubble.textContent = text;
    chat.append(bubble);
    scrollDown(bubble);
  };

  const addBotReply = () => {
    const label = document.createElement("div");
    label.className = "bot-label";
    label.textContent = "✣ Тихо · ваш ИИ-консьерж";

    const status = document.createElement("div");
    status.className = "reasoning";
    status.innerHTML = '<span class="spinner" aria-hidden="true"></span>Рассуждаю…';

    const body = document.createElement("p");
    body.className = "bot-text";

    chat.append(label, status, body);
    scrollDown(status);
    return { status, body };
  };

  const readEvents = async (response, onEvent) => {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop();
      for (const part of parts) {
        if (part.startsWith("data: ")) onEvent(JSON.parse(part.slice(6)));
      }
    }
  };

  const sendMessage = async () => {
    const text = input.value.trim();
    if (!text || sendButton.disabled) return;

    sendButton.disabled = true;
    sendButton.setAttribute("aria-busy", "true");
    addMessage(text, "user");
    input.value = "";
    input.style.height = "";
    const reply = addBotReply();

    try {
      const response = await fetch("/api/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, session_id: sessionId }),
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.detail || "Не удалось отправить сообщение");
      }
      await readEvents(response, (event) => {
        if (event.type === "token") {
          reply.status.hidden = true;
          reply.body.textContent += event.text;
          scrollDown(reply.body);
        } else if (event.type === "error") {
          throw new Error(event.detail);
        }
      });
    } catch (error) {
      reply.status.remove();
      if (!reply.body.textContent) reply.body.remove();
      addMessage(error.message, "error");
    } finally {
      reply.status.hidden = true;
      sendButton.disabled = false;
      sendButton.removeAttribute("aria-busy");
      input.focus();
    }
  };

  sendButton.addEventListener("click", sendMessage);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });
}
