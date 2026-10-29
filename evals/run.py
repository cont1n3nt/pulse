"""Прогон сценариев агента на настоящей модели.

    uv run python -m evals.run                 # все сценарии
    uv run python -m evals.run family_may buy  # выбранные
    uv run python -m evals.run --repeat 3      # каждый по 3 раза: видно нестабильность

Пишет таблицу и метрики качества (evals/metrics.py) в консоль и отчёт в evals/results/<время>.json
(и .md для README).
Если в окружении включена трассировка LangSmith (LANGSMITH_TRACING=true, LANGSMITH_API_KEY),
каждый ход виден в LangSmith: chat_id в метаданных = eval-<сценарий>-<время>.
"""

import argparse
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from backend.agent import forget_memory, get_memory, handle_message, reset_session
from backend.agent.service import collect, resume_checkout
from backend.agent.config import LLM_BASE_URL, LLM_MODEL
from backend.agent.location import HERE

from . import metrics
from .scenarios import SCENARIOS, Run, Scenario

RESULTS = Path(__file__).resolve().parent / "results"


def run_scenario(scenario: Scenario, stamp: str) -> tuple[Run, list[tuple[str, bool]], float]:
    user = f"eval-user-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    for i, text in enumerate(scenario.setup):
        handle_message(f"eval-{scenario.id}-{stamp}-setup{i}", text, user)
    session = f"eval-{scenario.id}-{stamp}"
    run = Run()
    for text in scenario.turns:
        turn_started = time.monotonic()
        if text == "<approve>":
            run.responses.append(collect(session, resume_checkout(session, True, user)))
        else:
            run.responses.append(handle_message(session, text, user))
        run.seconds.append(time.monotonic() - turn_started)
    run.memory = get_memory(user)["profile"]
    elapsed = time.monotonic() - started
    results = []
    for name, check in scenario.checks:
        try:
            ok = bool(check(run))
        except Exception:  # проверка упала — значит, ожидаемого состояния нет
            ok = False
        results.append((name, ok))
    reset_session(session)
    forget_memory(user)
    return run, results, elapsed


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Прогон сценариев агента")
    parser.add_argument("ids", nargs="*", help="id сценариев (по умолчанию все)")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    chosen = [s for s in SCENARIOS if not args.ids or s.id in args.ids]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    report, passed_checks, total_checks, passed_runs = [], 0, 0, 0
    for scenario in chosen:
        for attempt in range(args.repeat):
            run, results, elapsed = run_scenario(scenario, f"{stamp}-{attempt}")
            ok = all(r for _, r in results)
            passed_runs += ok
            passed_checks += sum(r for _, r in results)
            total_checks += len(results)
            mark = "PASS" if ok else "FAIL"
            print(f"[{mark}] {scenario.id} ({elapsed:.0f} с): {scenario.title}")
            for name, r in results:
                if not r:
                    print(f"       ✗ {name}")
            print("       ответ:", run.last.message.replace("\n", " ")[:300])
            report.append({"id": scenario.id, "title": scenario.title, "attempt": attempt, "passed": ok,
                           "seconds": round(elapsed, 1), "checks": [{"name": n, "ok": r} for n, r in results],
                           "metrics": metrics.of_run(run), "tools": run.tools,
                           "answers": [r.message for r in run.responses]})

    runs = len(chosen) * args.repeat
    summary = (f"Сценариев пройдено: {passed_runs}/{runs} ({passed_runs * 100 // max(runs, 1)}%), "
               f"проверок: {passed_checks}/{total_checks} ({passed_checks * 100 // max(total_checks, 1)}%)")
    quality = metrics.summary(report)
    print("\n" + summary)
    for name, value in quality:
        print(f"  {name}: {value}")
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{stamp}.json").write_text(json.dumps({"model": LLM_MODEL, "location": HERE.id, "summary": summary, "metrics": dict(quality), "runs": report},
                                                      ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"# Прогон {stamp}", "", f"Модель: `{LLM_MODEL}` ({LLM_BASE_URL}), локация: `{HERE.id}`.", "", summary, "",
             "## Метрики качества", "", "| Метрика | Значение |", "|---|---|"]
    lines += [f"| {name} | {value} |" for name, value in quality]
    lines += ["", "## Сценарии", "", "| Сценарий | Итог | Время | Уточнений | До варианта | Не прошло |",
              "|---|---|---|---|---|---|"]
    for item in report:
        failed = ", ".join(c["name"] for c in item["checks"] if not c["ok"]) or "—"
        m = item["metrics"]
        reach = f"{m['turns_to_option']} ход, {m['seconds_to_option']} с" if m["turns_to_option"] else "—"
        lines.append(f"| {item['title']} | {'✅' if item['passed'] else '❌'} | {item['seconds']} с | "
                     f"{m['clarifications']} | {reach} | {failed} |")
    (RESULTS / f"{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
