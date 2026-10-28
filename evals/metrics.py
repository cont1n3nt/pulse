"""Метрики качества агента по прогону: то, что трек просит измерять, а не только «прошло / не прошло».

- доля сценариев, доведённых до конца (все проверки сценария прошли);
- уточнения: сколько раз агент спросил, прежде чем показать варианты;
- время до первого варианта: ходов и секунд до первой выдачи пакетов или отелей;
- ошибки состыковок: пакеты с блокером по времени (прилёт после заезда, ночь без жилья и т. п.);
- ошибки хода: ответы с блоком error (модель или инструмент упали).
"""

from statistics import median

from .scenarios import Run, asks_question

CONNECTION = ("late_arrival", "early_arrival", "late_departure", "flights_order", "dates_mismatch")


def of_run(run: Run) -> dict:
    first = next((i for i, r in enumerate(run.responses)
                  if any(b.type in ("packages", "hotels") for b in r.blocks)), None)
    before = run.responses[:first] if first is not None else run.responses
    shown = [p for r in run.responses for b in r.blocks if b.type == "packages" for p in b.data.get("items", [])]
    broken = [p for p in shown if any(i["severity"] == "blocker" and i["code"] in CONNECTION for i in p.get("issues", []))]
    return {
        "turns_to_option": first + 1 if first is not None else None,
        "seconds_to_option": round(sum(run.seconds[:first + 1]), 1) if first is not None else None,
        "clarifications": sum(asks_question(Run([r])) for r in before),
        "packages_shown": len(shown),
        "connection_errors": len(broken),
        "errors": sum(b.type == "error" for r in run.responses for b in r.blocks),
        "tools_per_turn": round(len(run.tools) / max(len(run.responses), 1), 1),
    }


def summary(items: list[dict]) -> list[tuple[str, str]]:
    """Сводка по всем прогонам: (метрика, значение) для консоли и отчёта."""
    runs = len(items)
    reached = [m for m in (i["metrics"] for i in items) if m["turns_to_option"] is not None]
    shown = sum(i["metrics"]["packages_shown"] for i in items)
    broken = sum(i["metrics"]["connection_errors"] for i in items)
    out = [("Сценарии доведены до конца", f"{sum(i['passed'] for i in items)}/{runs}")]
    if reached:
        out += [
            ("Дошли до вариантов", f"{len(reached)}/{runs}"),
            ("Уточняющих вопросов до вариантов, в среднем", f"{sum(m['clarifications'] for m in reached) / len(reached):.1f}"),
            ("Ходов до первого варианта, медиана", f"{median(m['turns_to_option'] for m in reached):g}"),
            ("Секунд до первого варианта, медиана", f"{median(m['seconds_to_option'] for m in reached):.0f}"),
        ]
    out += [
        ("Пакетов без ошибок состыковки", f"{shown - broken}/{shown}"),
        ("Ходов с ошибкой модели или инструмента", str(sum(i["metrics"]["errors"] for i in items))),
        ("Вызовов инструментов на ход, в среднем",
         f"{sum(i['metrics']['tools_per_turn'] for i in items) / max(runs, 1):.1f}"),
    ]
    return out
