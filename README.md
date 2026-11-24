# Аудит Яндекс Директа — скилл для Claude

Скилл ведёт человека от «у меня нет доступа к API» до готового аудита рекламы:

1. **Приложение на Яндекс OAuth** — через браузер открывает форму, подсказывает, что заполнить (право `direct:api`).
2. **Заявка на доступ к API** в Директе — заполняет форму готовыми текстами под ваш случай, показывает вам и отправляет только после «да».
3. **Токен** — открывает страницу выдачи, токен копируете вы сами.
4. **Выгрузка и аудит** — скрипт только читает данные (кампании, объявления, ключи, поисковые запросы, площадки РСЯ), нейросеть проходит чек-лист и выдаёт отчёт: где сливается бюджет, минус-слова, площадки для запрета, план правок.

Логику задают инструкции в `SKILL.md` и `references/`, а не код. Код — два небольших скрипта.

## Установка

Скопируйте папку `yandex-direct-audit` в папку скиллов:

- Claude Code: `~/.claude/skills/yandex-direct-audit` (для всех проектов) или `.claude/skills/yandex-direct-audit` в проекте.
- Claude.ai / Desktop: заархивируйте папку в zip и загрузите в Settings → Capabilities → Skills.

Потом просто напишите: «Хочу проаудировать свой Директ».

## Браузер

- Если у Claude есть браузер (Claude in Chrome, встроенный браузер, Playwright MCP) — скилл использует его, вы уже залогинены в Яндексе.
- Если нет — `scripts/setup_access.py` на открытом [browser-use](https://github.com/browser-use/browser-use) (MIT): видимое окно Chromium, ИИ-агент заполняет формы и спрашивает вас в терминале.
  ```bash
  pip install browser-use && export ANTHROPIC_API_KEY=...
  python yandex-direct-audit/scripts/setup_access.py --step app
  python yandex-direct-audit/scripts/setup_access.py --step request --client-id <ClientID>
  python yandex-direct-audit/scripts/setup_access.py --step token --client-id <ClientID>
  ```
- Без браузера — Claude ведёт по шагам текстом.

## Выгрузка вручную

```bash
pip install requests
export YANDEX_DIRECT_TOKEN='...'
python yandex-direct-audit/scripts/direct_fetch.py --days 30            # свой аккаунт
python yandex-direct-audit/scripts/direct_fetch.py --client-login client # клиент агентства
```

## Безопасность

- Пароль, SMS-коды и капчу вводит только человек.
- Ничего не создаётся и не отправляется без явного подтверждения.
- Аудит не меняет кампании: только методы `get` и Reports.
- Токен не выводится в чат и не коммитится (`.env` в `.gitignore`).

## Что нужно знать

- Для страницы «Настройки API» в Директе нужна хотя бы одна кампания.
- Заявку рассматривают от нескольких часов до нескольких рабочих дней.
- Формы Яндекса меняются: скилл читает живую страницу и сверяется с официальной документацией: [регистрация приложения](https://yandex.ru/dev/direct/doc/ru/register), [заявка на доступ](https://yandex.ru/dev/direct/doc/ru/access-request).
