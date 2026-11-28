# Формат выгрузки и чтение

## Два варианта файла

**Полный экспорт аккаунта** (`result.json`):

```json
{
  "personal_information": {"user_id": 111, "first_name": "...", "username": "@..."},
  "contacts": {"list": [{"first_name": "...", "last_name": "...", "phone_number": "...", "date": "..."}]},
  "chats": {"list": [
    {"name": "Анна К", "type": "personal_chat", "id": 222, "messages": [ ... ]}
  ]}
}
```

**Экспорт одного чата**: сразу `{"name": ..., "type": "personal_chat", "id": ..., "messages": [...]}`.

## Типы чатов

- `personal_chat` — личный чат. **Берём только их.**
- `saved_messages` — «Избранное», `bot_chat` — боты, `private_group`, `private_supergroup`, `public_supergroup`, `private_channel`, `public_channel` — пропускаем.

## Сообщение

```json
{"id": 1, "type": "message", "date": "2023-03-01T10:00:00", "from": "Анна К", "from_id": "user222",
 "text": "строка или список частей", "text_entities": [...]}
```

- `type: "service"` — звонки, закрепления и прочие служебные события. Пропускаем, но звонок (`action: "phone_call"`) — признак тёплого контакта, можно учесть.
- `text` бывает строкой или списком из строк и объектов `{"type": "bold", "text": "..."}` — склеивай.
- Владелец аккаунта — `from_id == "user" + personal_information.user_id`. В экспорте одного чата владельца нет: «я» — тот, чьё `from` не совпадает с именем чата.
- Username собеседника в выгрузке обычно нет. Для связи используй имя чата и `id`; человек найдёт собеседника по имени в своём Telegram.

## Код для дайджеста

Пиши на ходу, если среда позволяет. Пример, который можно взять за основу:

```python
import json
from datetime import datetime

def text_of(t):
    if isinstance(t, str):
        return t
    return "".join(p if isinstance(p, str) else p.get("text", "") for p in t or [])

def load_chats(path):
    d = json.load(open(path, encoding="utf-8"))
    if "chats" in d:
        return d.get("personal_information", {}).get("user_id"), d["chats"]["list"]
    return None, [d]

def digest(path, since=None, head=5, tail=25, cut=300):
    owner, chats = load_chats(path)
    out = []
    for c in chats:
        if c.get("type") != "personal_chat":
            continue
        msgs = [m for m in c.get("messages", [])
                if m.get("type") == "message" and text_of(m.get("text")).strip()]
        if since:
            msgs = [m for m in msgs if m["date"][:10] >= since]
        if not msgs:
            continue
        me_ids = {f"user{owner}"} if owner else {m["from_id"] for m in msgs if m.get("from") != c.get("name")}
        if not [m for m in msgs if m.get("from_id") not in me_ids]:
            continue  # собеседник ни разу не ответил
        pick = msgs if len(msgs) <= head + tail else msgs[:head] + msgs[-tail:]
        out.append({
            "chat_id": c.get("id"), "name": c.get("name"),
            "messages_total": len(msgs),
            "first_date": msgs[0]["date"][:10], "last_date": msgs[-1]["date"][:10],
            "last_from": "я" if msgs[-1].get("from_id") in me_ids else "собеседник",
            "days_since_last": (datetime.now() - datetime.fromisoformat(msgs[-1]["date"])).days,
            "dialog": [("я" if m.get("from_id") in me_ids else "он/она") + " " + m["date"][:10] + ": "
                       + text_of(m["text"])[:cut] for m in pick],
        })
    return sorted(out, key=lambda x: x["last_date"], reverse=True)
```

Код только собирает дайджест. Читать `dialog` и решать, кто это и чего хотел, — тебе.

## Несколько файлов

Если прислали выгрузки с разных аккаунтов — прочитай каждую, добавь поле «аккаунт» и объедини. Если один и тот же человек есть в двух аккаунтах (совпадают имя и тема), объедини в одну строку и возьми самую свежую дату.
