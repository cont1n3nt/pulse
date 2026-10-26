const chat = document.querySelector(".chat");
const form = document.querySelector(".chat-form");
const input = form?.querySelector("textarea");
const sendButton = form?.querySelector(".send");
const chatList = document.querySelector(".chat-list");
const newChatButton = document.querySelector(".new-chat");
const chatTitle = document.querySelector("#chat-title");

if (chat && form && input && sendButton && chatList && newChatButton && chatTitle) {
  input.id = "chat-input";
  input.setAttribute("aria-label", "Текст сообщения");
  input.removeAttribute("readonly");
  sendButton.type = "button";

  const STORAGE_KEY = "pulse.chats.v1";
  const starterChats = [
    { id: crypto.randomUUID(), title: "Сентябрь у моря", subtitle: "Сочи · 14–19 сентября", messages: [] },
    { id: crypto.randomUUID(), title: "Выходные в Казани", subtitle: "Вчера", messages: [] },
    { id: crypto.randomUUID(), title: "Петербург вдвоём", subtitle: "21 августа", messages: [] },
  ];
  let chats;
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    chats = Array.isArray(saved) && saved.length ? saved : starterChats;
  } catch {
    chats = starterChats;
  }
  chats = chats.filter((item) => item && typeof item.id === "string" && Array.isArray(item.messages));
  if (!chats.length) chats = starterChats;
  let activeChatId = chats[0].id;
  let sending = false;

  const persist = () => localStorage.setItem(STORAGE_KEY, JSON.stringify(chats));
  const activeChat = () => chats.find((item) => item.id === activeChatId);

  const scrollDown = (el) => el.scrollIntoView({ block: "nearest" });

  const renderMessage = (text, className) => {
    const bubble = document.createElement("div");
    bubble.className = `bubble ${className}`;
    bubble.textContent = text;
    chat.append(bubble);
    return bubble;
  };

  const renderChatList = () => {
    chatList.replaceChildren();
    chats.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `trip${item.id === activeChatId ? " active" : ""}`;
      button.setAttribute("aria-current", item.id === activeChatId ? "true" : "false");
      button.append(document.createTextNode(item.title));
      const subtitle = document.createElement("small");
      subtitle.textContent = item.subtitle || "Новая переписка";
      button.append(subtitle);
      button.addEventListener("click", () => switchChat(item.id));
      chatList.append(button);
    });
  };

  const renderChat = () => {
    const current = activeChat();
    chat.replaceChildren();
    chatTitle.textContent = current.title;
    current.messages.forEach((item) => renderMessage(item.text, item.role));
    if (chat.lastElementChild) scrollDown(chat.lastElementChild);
    renderChatList();
  };

  const switchChat = (id) => {
    if (sending || !chats.some((item) => item.id === id)) return;
    activeChatId = id;
    renderChat();
  };

  const createChat = () => {
    const item = {
      id: crypto.randomUUID(),
      title: "Новая поездка",
      subtitle: "Новая переписка",
      messages: [],
    };
    chats.unshift(item);
    activeChatId = item.id;
    persist();
    renderChat();
  };

  const sendMessage = async () => {
    const text = input.value.trim();
    if (!text || sending) return;

    sending = true;
    sendButton.disabled = true;
    sendButton.setAttribute("aria-busy", "true");
    const current = activeChat();
    current.messages.push({ role: "user", text });
    if (current.title === "Новая поездка") current.title = text.slice(0, 32);
    persist();
    renderChat();
    input.value = "";
    input.style.height = "";

    try {
      const response = await fetch("/api/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: current.id, messages: current.messages }),
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.detail || "Не удалось отправить сообщение");
      }
      const result = await response.json();
      current.messages.push({ role: "assistant", text: result.text });
      persist();
      if (activeChatId === current.id) renderChat();
    } catch (error) {
      renderMessage(error.message, "error");
    } finally {
      sending = false;
      sendButton.disabled = false;
      sendButton.removeAttribute("aria-busy");
      input.focus();
    }
  };

  sendButton.addEventListener("click", sendMessage);
  newChatButton.addEventListener("click", createChat);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });
  persist();
  renderChat();
}
