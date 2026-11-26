# Формат выгрузки и код

## Файл

- **Полный экспорт**: `{"personal_information": {"user_id": 111, ...}, "chats": {"list": [ ...чаты... ]}}`.
- **Экспорт одного чата**: сразу `{"name": ..., "type": ..., "id": ..., "messages": [...]}`. Таких файлов может быть несколько — обрабатывай каждый и объединяй.

Типы чатов, которые берём: `private_group`, `private_supergroup`, `public_supergroup` (рабочие группы) и `personal_chat` (личка с клиентом). Каналы, ботов и «Избранное» пропускаем.

Сообщение: `{"id", "type": "message", "date": "2026-11-18T10:00:00", "from": "Имя", "from_id": "user501", "text": строка или список частей}`. Служебные (`type: "service"`) пропускаем. `text` склеивай из частей.

## Код

Пиши на ходу, если среда позволяет. Пример, который можно взять за основу:

```python
import json
from datetime import datetime, timedelta

GROUPS = {"personal_chat", "private_group", "private_supergroup", "public_supergroup"}

def text_of(t):
    if isinstance(t, str):
        return t
    return "".join(p if isinstance(p, str) else p.get("text", "") for p in t or [])

def load_chats(path):
    d = json.load(open(path, encoding="utf-8"))
    if "chats" in d:
        return d.get("personal_information", {}).get("user_id"), d["chats"]["list"]
    return None, [d]

def list_chats(path):
    """Список чатов, чтобы человек выбрал проектные."""
    _, chats = load_chats(path)
    out = []
    for c in chats:
        if c.get("type") not in GROUPS:
            continue
        msgs = [m for m in c.get("messages", []) if m.get("type") == "message"]
        if msgs:
            out.append({"chat_id": c.get("id"), "name": c.get("name"), "type": c.get("type"),
                        "messages": len(msgs), "last_date": msgs[-1]["date"][:10]})
    return sorted(out, key=lambda x: x["last_date"], reverse=True)

def participants(path, chat_ids):
    """Кто писал в выбранных чатах — чтобы человек отметил свою команду."""
    _, chats = load_chats(path)
    seen = {}
    for c in chats:
        if c.get("id") in chat_ids:
            for m in c.get("messages", []):
                if m.get("type") == "message" and m.get("from_id"):
                    seen.setdefault(m["from_id"], {"name": m.get("from"), "messages": 0})
                    seen[m["from_id"]]["messages"] += 1
    return seen

def digest(path, chat_ids, team_ids, days=14, now=None, limit=300, cut=400):
    """Сообщения выбранных чатов за период и время ответа команды на сообщения клиента."""
    owner, chats = load_chats(path)
    team = set(team_ids) | ({f"user{owner}"} if owner else set())
    now = now or datetime.now()
    since = (now - timedelta(days=days)).isoformat()
    out = []
    for c in chats:
        if c.get("id") not in chat_ids:
            continue
        msgs = [m for m in c.get("messages", [])
                if m.get("type") == "message" and text_of(m.get("text")).strip() and m["date"] >= since]
        if not msgs:
            continue
        lines, waits = [], []
        for i, m in enumerate(msgs):
            who = "команда" if m.get("from_id") in team else "клиент"
            lines.append(f'{m["date"][:16].replace("T", " ")} · {who} · {m.get("from")}: {text_of(m["text"])[:cut]}')
            if who == "клиент":
                nxt = next((x for x in msgs[i + 1:] if x.get("from_id") in team), None)
                end = datetime.fromisoformat(nxt["date"]) if nxt else now
                waits.append({"msg_id": m["id"], "date": m["date"][:16],
                              "hours": round((end - datetime.fromisoformat(m["date"])).total_seconds() / 3600, 1),
                              "answered": bool(nxt)})
        last = msgs[-1]
        out.append({"chat_id": c.get("id"), "name": c.get("name"),
                    "last_from": "команда" if last.get("from_id") in team else "клиент",
                    "hours_since_last": round((now - datetime.fromisoformat(last["date"])).total_seconds() / 3600, 1),
                    "client_waits": waits, "dialog": lines[-limit:]})
    return out
```

- `client_waits` — сколько часов прошло от сообщения клиента до первого ответа команды. Это просто арифметика: **нужен ли ответ на это сообщение** («спасибо», «ок», стикер — не нужен), решаешь ты, читая `dialog`.
- Часы календарные. Если человек считает рабочие часы — учитывай ночи и выходные, когда оцениваешь ожидание.
- Если в чате за период больше 300 сообщений, `dialog` содержит последние 300. Скажи об этом и при необходимости сократи период.
