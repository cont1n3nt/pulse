"""
Из корня проекта:
    uv run python -m docs.cli
"""

from backend.agent import handle_message

SESSION = "terminal"

print("Тревел-агент. Выход: exit")
while True:
    text = input("\nТы: ").strip()
    if text.lower() in {"exit", "quit", "выход"}:
        break
    if not text:
        continue
    resp = handle_message(SESSION, text)
    if resp.tool_calls:
        print("  [tools]", ", ".join(resp.tool_calls))
    for b in resp.blocks:
        print(f"  [block] {b.type}: {len(b.data.get('items', []))} шт.")
    print("\nАгент:", resp.message)
