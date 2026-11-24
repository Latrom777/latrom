#!/usr/bin/env python3
"""Браузерный помощник для доступа к API Яндекс Директа на базе browser-use
(https://github.com/browser-use/browser-use, MIT).

Открывает видимое окно Chromium. ИИ-агент проходит шаги, а на входе в аккаунт,
на непонятных полях и перед отправкой формы спрашивает вас в терминале.
Пароль и коды вводите только вы, прямо в окне браузера.

  pip install browser-use            # Python >= 3.11
  export ANTHROPIC_API_KEY=...
  python setup_access.py --step app
  python setup_access.py --step request --client-id <ClientID>
  python setup_access.py --step token   --client-id <ClientID>
"""

import argparse
import asyncio
from pathlib import Path

from browser_use import Agent, Browser, Tools

REFS = Path(__file__).resolve().parent.parent / "references"
PROFILE = Path.home() / ".yandex-direct-audit-browser"

RULES = """
Правила, которые нельзя нарушать:
- Никогда не вводи логин, пароль, SMS/2FA-коды и не решай капчу. Если нужен вход в Яндекс —
  вызови ask_human и попроси человека войти в открытом окне, затем продолжай.
- Не выдумывай данные человека (компания, сайт, почта, телефон, ФИО). Если поле этого требует — спроси через ask_human.
- Перед нажатием любой кнопки, которая создаёт или отправляет что-то («Создать приложение», «Отправить»,
  «Подать заявку», «Сохранить», «Принимаю»), вызови ask_human со списком ВСЕХ заполненных полей и значений
  и спроси «Отправляем? (да/нет)». Жми только при ответе «да». При другом ответе — исправь, как скажет человек.
- Интерфейс мог измениться: ориентируйся по смыслу полей на странице, а не по точным названиям.
- Не печатай в итоговый ответ Client secret и OAuth-токены.
"""

TASKS = {
    "app": """
Задача: создать приложение на Яндекс OAuth для доступа к API Яндекс Директа.
1. Открой https://oauth.yandex.ru/client/new . Если просит войти — ask_human.
2. Спроси через ask_human, какое название сервиса и какую почту для связи указать
   (предложи вариант названия «Аудит рекламы <компания>»).
3. Тип платформы — «Для доступа к API или отладки». Если такого нет — «Веб-сервисы»
   и Redirect URI https://oauth.yandex.ru/verification_code .
4. В доступах к данным найди «Яндекс Директ» и отметь только «Использование API Яндекс Директа» (direct:api).
5. Покажи всё через ask_human, по «да» создай приложение.
6. В итоговом ответе верни ClientID созданного приложения (только ClientID).
Справка по полям:
{oauth_ref}
""",
    "request": """
Задача: подать заявку на доступ к API Яндекс Директа для приложения с ClientID {client_id}.
1. Открой https://direct.yandex.ru/registered/main.pl?cmd=apiSettings . Если просит войти — ask_human.
   Если страница пишет, что нужно создать кампанию — сообщи человеку через ask_human и закончи.
2. Если показано пользовательское соглашение API — через ask_human попроси человека прочитать его и
   подтвердить согласие; принимай только по «да».
3. Вкладка «Мои заявки» → новая заявка. Если заявка для этого приложения уже есть — верни её статус и закончи.
4. Прочитай форму целиком. Выбери приложение {client_id}. Все данные человека (компания, сайт, почта, контакты,
   свои кампании или агентство) спроси одним вопросом через ask_human.
5. Описание заполни по шаблонам из справки, подставив ответы человека.
6. Покажи все поля через ask_human, по «да» отправь.
7. В итоговом ответе верни: отправлена ли заявка и её статус.
Справка:
{request_ref}
""",
    "token": """
Задача: помочь человеку получить OAuth-токен для приложения {client_id}.
1. Открой https://oauth.yandex.ru/authorize?response_type=token&client_id={client_id} . Если просит войти — ask_human.
2. Через ask_human попроси человека проверить, что выбран нужный аккаунт, и самому нажать «Разрешить».
3. Когда токен показан на странице — НЕ копируй и не выводи его. Через ask_human скажи человеку скопировать
   токен самому и выполнить в терминале: export YANDEX_DIRECT_TOKEN='...'
4. В итоговом ответе напиши только «токен получен» или в чём проблема.
""",
}


def make_llm(name: str):
    provider, _, model = name.partition(":")
    from browser_use import ChatAnthropic, ChatGoogle, ChatOpenAI

    if provider == "anthropic":
        return ChatAnthropic(model or "claude-sonnet-5-5")
    if provider == "openai":
        return ChatOpenAI(model or "gpt-5")
    if provider == "google":
        return ChatGoogle(model or "gemini-2.5-pro")
    raise SystemExit("--llm: anthropic[:model] | openai[:model] | google[:model]")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step", choices=TASKS, required=True)
    ap.add_argument("--client-id", help="ClientID приложения (для шагов request и token)")
    ap.add_argument("--llm", default="anthropic", help="anthropic[:model] | openai[:model] | google[:model]")
    args = ap.parse_args()
    if args.step != "app" and not args.client_id:
        ap.error("для этого шага нужен --client-id")

    task = TASKS[args.step].format(
        client_id=args.client_id,
        oauth_ref=(REFS / "oauth-app.md").read_text(encoding="utf-8"),
        request_ref=(REFS / "access-request.md").read_text(encoding="utf-8"),
    )

    tools = Tools()

    @tools.action("Спросить человека: вход в аккаунт, данные для полей, подтверждение перед отправкой. Возвращает ответ.")
    async def ask_human(question: str) -> str:
        print(f"\n🤖 {question}")
        answer = await asyncio.to_thread(input, "Вы: ")
        return answer.strip() or "(пусто)"

    browser = Browser(
        "chromium",  # локальный видимый браузер, не облачный
        headless=False,
        user_data_dir=str(PROFILE),
        allowed_domains=["*.yandex.ru", "yandex.ru", "*.yandex.com", "yandex.com", "*.ya.ru", "ya.ru"],
    )
    agent = Agent(
        task=task,
        llm=make_llm(args.llm),
        browser=browser,
        tools=tools,
        extend_system_message=RULES,
        judge=False,
    )
    history = await agent.run(max_steps=80, timeout=3600)
    print("\nРезультат:", history.final_result())


if __name__ == "__main__":
    asyncio.run(main())
