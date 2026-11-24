#!/usr/bin/env python3
"""Оценка кабинета Директа по чек-листу (0–100 и грейд A–F).

Проверки, их категории и важность читаются из таблицы в references/audit-checklist.md.

На вход — JSON со статусами:  {"M1": "PASS", "B1": "FAIL", "K5": "NA", ...}
Статусы: PASS = 1, WARN = 0.5, FAIL = 0, NA = не учитывается.
Проверки, которых нет в JSON, считаются NA.

  python score.py results.json           # или:  echo '{...}' | python score.py -
  python score.py results.json --json    # машиночитаемый результат
"""

import json
import re
import sys
from pathlib import Path

CHECKLIST = Path(__file__).resolve().parent.parent / "references" / "audit-checklist.md"
SEVERITY = {"critical": 5.0, "high": 3.0, "medium": 1.5, "low": 0.5}
STATUS = {"PASS": 1.0, "WARN": 0.5, "FAIL": 0.0}
GRADES = [(90, "A", "Отлично"), (75, "B", "Хорошо"), (60, "C", "Требует внимания"), (40, "D", "Плохо"), (0, "F", "Критично")]


def load_checklist():
    text = CHECKLIST.read_text(encoding="utf-8")
    weights, names, checks = {}, {}, {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and re.fullmatch(r"[A-Z]", cells[0]) and cells[2].isdigit():
            names[cells[0]], weights[cells[0]] = cells[1], float(cells[2])
        elif len(cells) >= 4 and re.fullmatch(r"[A-Z]\d+", cells[0]) and cells[2] in SEVERITY:
            checks[cells[0]] = {"cat": cells[1], "sev": cells[2], "title": cells[3]}
    if not weights or not checks:
        sys.exit(f"Не удалось прочитать таблицы из {CHECKLIST}")
    return weights, names, checks


def grade(score: float):
    for low, letter, label in GRADES:
        if score >= low:
            return letter, label
    return GRADES[-1][1:]


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    if not args:
        sys.exit(__doc__)
    raw = sys.stdin.read() if args[0] == "-" else Path(args[0]).read_text(encoding="utf-8")
    results = {k.upper(): str(v).upper().strip() for k, v in json.loads(raw).items()}

    weights, names, checks = load_checklist()
    unknown = sorted(set(results) - set(checks))
    bad = sorted(k for k, v in results.items() if v not in STATUS and v not in ("NA", "N/A"))
    if bad:
        sys.exit(f"Неизвестные статусы у {', '.join(bad)} — допустимо PASS/WARN/FAIL/NA")

    got = total = 0.0
    cats = {}
    for cid, c in checks.items():
        st = results.get(cid, "NA")
        cat = cats.setdefault(c["cat"], {"got": 0.0, "max": 0.0, "all": 0.0, "fails": [], "warns": []})
        w = SEVERITY[c["sev"]] * weights.get(c["cat"], 0)
        cat["all"] += SEVERITY[c["sev"]]
        if st not in STATUS:
            continue
        cat["max"] += SEVERITY[c["sev"]]
        cat["got"] += STATUS[st] * SEVERITY[c["sev"]]
        got += STATUS[st] * w
        total += w
        if st == "FAIL":
            cat["fails"].append(cid)
        elif st == "WARN":
            cat["warns"].append(cid)

    score = round(got / total * 100) if total else 0
    letter, label = grade(score)
    out = {"score": score, "grade": letter, "label": label, "categories": {}, "unknown_ids": unknown}
    for code in weights:
        c = cats.get(code, {"got": 0, "max": 0, "all": 0, "fails": [], "warns": []})
        cs = round(c["got"] / c["max"] * 100) if c["max"] else None
        out["categories"][code] = {
            "name": names[code],
            "score": cs,
            "incomplete": c["all"] > 0 and (c["all"] - c["max"]) / c["all"] > 0.3,
            "fails": c["fails"],
            "warns": c["warns"],
        }

    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    print(f"**Оценка кабинета: {score}/100 — {letter} ({label})**\n")
    print("| Категория | Вес | Балл | Проблемы | Замечания |")
    print("|---|---|---|---|---|")
    for code, c in out["categories"].items():
        s = "нет данных" if c["score"] is None else f"{c['score']}/100" + (" (оценка неполная)" if c["incomplete"] else "")
        print(f"| {c['name']} | {weights[code]:.0f}% | {s} | {', '.join(c['fails']) or '—'} | {', '.join(c['warns']) or '—'} |")
    if "P" in out["categories"] and out["categories"]["P"]["score"] is None:
        print("\n_Оценка без учёта продаж: файл продаж не предоставлен, категория «Продажи и окупаемость» не оценивалась._")
    if total == 0:
        print("\nНи одна проверка не оценена — оценка не имеет смысла.")
    if unknown:
        print(f"\nНеизвестные ID проигнорированы: {', '.join(unknown)}")


if __name__ == "__main__":
    main()
