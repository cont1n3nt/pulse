"""
Из корня проекта:
    uv run python -m docs.cli
"""

from backend.agent import handle_message, resume_checkout
from backend.agent.service import collect

SESSION = "terminal"


def show(resp) -> None:
    if resp.tool_calls:
        print("  [tools]", ", ".join(resp.tool_calls))
    for b in resp.blocks:
        if b.type == "cart":
            print(f"  [корзина] итого {b.data['total_rub']} ₽, проблем: {len(b.data['issues'])}")
        elif b.type == "order":
            print(f"  [заказ] {b.data['order']['id']}")
        elif b.type != "confirm":
            print(f"  [block] {b.type}: {len(b.data.get('items', []))} шт.")
    print("\nАгент:", resp.message)
    confirm = next((b for b in resp.blocks if b.type == "confirm"), None)
    if confirm:
        print(f"\n  Подтвердить оплату {confirm.data['total_rub']} ₽ (имитация)?")
        for issue in confirm.data["issues"]:
            print(f"   ! [{issue['severity']}] {issue['message']}")
        approved = input("  [y/n]: ").strip().lower() in {"y", "д", "да", "yes"}
        show(collect(SESSION, resume_checkout(SESSION, approved)))


print("Тревел-агент. Выход: exit")
while True:
    text = input("\nТы: ").strip()
    if text.lower() in {"exit", "quit", "выход"}:
        break
    if not text:
        continue
    show(handle_message(SESSION, text))
