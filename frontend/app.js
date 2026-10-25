const chat = document.querySelector(".chat");
const form = document.querySelector(".chat-form");
const input = form?.querySelector("textarea");
const sendButton = form?.querySelector(".send");

if (chat && form && input && sendButton) {
  input.id = "chat-input";
  input.setAttribute("aria-label", "Текст сообщения");
  input.removeAttribute("readonly");
  sendButton.type = "button";

  const addMessage = (text, className) => {
    const bubble = document.createElement("div");
    bubble.className = `bubble ${className}`;
    bubble.textContent = text;
    chat.append(bubble);
    bubble.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  const sendMessage = async () => {
    const text = input.value.trim();
    if (!text || sendButton.disabled) return;

    sendButton.disabled = true;
    sendButton.setAttribute("aria-busy", "true");
    try {
      const response = await fetch("/api/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Не удалось отправить сообщение");
      addMessage(result.text, "user");
      input.value = "";
      input.style.height = "";
    } catch (error) {
      addMessage(`Не удалось отправить сообщение: ${error.message}`, "error");
    } finally {
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
