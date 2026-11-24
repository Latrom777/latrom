#!/usr/bin/env python3
"""Выгрузка данных Яндекс Директа для аудита. Только чтение.

Токен: переменная окружения YANDEX_DIRECT_TOKEN или строка YANDEX_DIRECT_TOKEN=... в файле .env
(в текущей папке или рядом со скриптом).

  python direct_fetch.py --days 30 --out direct_audit_data
  python direct_fetch.py --client-login my-client     # аккаунт клиента агентства
  python direct_fetch.py --sandbox                    # песочница (тестовый доступ)
"""

import argparse
import csv
import io
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

PROD = "https://api.direct.yandex.com/json/v5/"
SANDBOX = "https://api-sandbox.direct.yandex.com/json/v5/"


def load_token() -> str:
    token = os.environ.get("YANDEX_DIRECT_TOKEN", "").strip()
    if token:
        return token
    for env in (Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"):
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition("=")
                if key.strip() == "YANDEX_DIRECT_TOKEN" and value.strip():
                    return value.strip().strip("'\"")
    sys.exit("Нет токена: задайте YANDEX_DIRECT_TOKEN (export или .env). См. этап 3 в SKILL.md.")


class ApiError(Exception):
    pass


class Direct:
    def __init__(self, token: str, base: str, client_login: str | None):
        self.base = base
        self.s = requests.Session()
        self.s.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept-Language": "ru",
                "Content-Type": "application/json; charset=utf-8",
            }
        )
        if client_login:
            self.s.headers["Client-Login"] = client_login
        self.units = ""

    def call(self, service: str, params: dict) -> dict:
        for attempt in range(5):
            r = self.s.post(self.base + service, json={"method": "get", "params": params}, timeout=120)
            self.units = r.headers.get("Units", self.units)
            if r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            data = r.json()
            if "error" in data:
                e = data["error"]
                code = int(e.get("error_code", 0))
                if code in (52, 56, 506, 1000, 1001, 1002) and attempt < 4:  # перегрузка/лимиты — подождать
                    time.sleep(5 * (attempt + 1))
                    continue
                raise ApiError(f"{service}: {code} {e.get('error_string')} — {e.get('error_detail')}")
            return data["result"]
        raise ApiError(f"{service}: сервер недоступен")

    def get_all(self, service: str, key: str, params: dict) -> list:
        """Все страницы результата."""
        items, offset = [], 0
        while True:
            page = dict(params, Page={"Limit": 10000, "Offset": offset})
            res = self.call(service, page)
            items.extend(res.get(key, []))
            if "LimitedBy" not in res:
                return items
            offset = res["LimitedBy"]

    def get_robust(self, service: str, key: str, params: dict) -> list:
        """Если доп. наборы полей не поддерживаются — повторить только с базовыми FieldNames."""
        try:
            return self.get_all(service, key, params)
        except ApiError as e:
            extra = [k for k in params if k.endswith("FieldNames") and k != "FieldNames"]
            if not extra:
                raise
            warn(f"{e}. Повторяю без {', '.join(extra)}")
            return self.get_all(service, key, {k: v for k, v in params.items() if k not in extra})

    def report(self, name: str, rtype: str, fields: list, d_from: str, d_to: str) -> str:
        body = {
            "params": {
                "SelectionCriteria": {"DateFrom": d_from, "DateTo": d_to},
                "FieldNames": fields,
                "ReportName": f"{name} {d_from} {d_to} {int(time.time())}",
                "ReportType": rtype,
                "DateRangeType": "CUSTOM_DATE",
                "Format": "TSV",
                "IncludeVAT": "YES",
            }
        }
        headers = {
            "processingMode": "auto",
            "returnMoneyInMicros": "false",
            "skipReportHeader": "true",
            "skipReportSummary": "true",
        }
        for _ in range(60):
            r = self.s.post(self.base + "reports", json=body, headers=headers, timeout=300)
            if r.status_code == 200:
                r.encoding = "utf-8"
                return r.text
            if r.status_code in (201, 202):
                time.sleep(int(r.headers.get("retryIn", 10)))
                continue
            if r.status_code == 502 or r.status_code >= 500:
                time.sleep(10)
                continue
            try:
                e = r.json()["error"]
                msg = f"{e.get('error_code')} {e.get('error_string')} — {e.get('error_detail')}"
            except Exception:
                msg = r.text[:300]
            raise ApiError(f"Отчёт {rtype}: HTTP {r.status_code} {msg}")
        raise ApiError(f"Отчёт {rtype}: не дождались готовности")


def warn(msg: str) -> None:
    print(f"  ! {msg}", file=sys.stderr)


def chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def by_campaigns(api: Direct, service: str, key: str, ids: list, params: dict) -> list:
    out = []
    for part in chunks(ids, 10):
        p = dict(params, SelectionCriteria={"CampaignIds": part})
        try:
            out.extend(api.get_robust(service, key, p))
        except ApiError as e:
            warn(str(e))
    return out


CONV = ["Conversions", "CostPerConversion"]
REPORTS = {
    # имя файла: (тип отчёта, поля)
    "campaigns": (
        "CAMPAIGN_PERFORMANCE_REPORT",
        ["CampaignId", "CampaignName", "CampaignType", "Impressions", "Clicks", "Ctr", "Cost", "AvgCpc", "BounceRate"] + CONV,
    ),
    "criteria": (
        "CRITERIA_PERFORMANCE_REPORT",
        ["CampaignName", "AdGroupName", "CriterionId", "Criterion", "CriterionType", "AdNetworkType",
         "Impressions", "Clicks", "Ctr", "Cost", "AvgCpc", "BounceRate"] + CONV,
    ),
    "search_queries": (
        "SEARCH_QUERY_PERFORMANCE_REPORT",
        ["Query", "CampaignName", "AdGroupName", "Criterion", "Impressions", "Clicks", "Ctr", "Cost", "AvgCpc", "BounceRate"] + CONV,
    ),
    "placements": (
        "CUSTOM_REPORT",
        ["Placement", "AdNetworkType", "CampaignName", "Impressions", "Clicks", "Ctr", "Cost", "AvgCpc", "BounceRate"] + CONV,
    ),
}


def num(v) -> float:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def read_tsv(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text), delimiter="\t")) if text.strip() else []


def table(rows: list[dict], cols: list[str], limit: int) -> str:
    rows = rows[:limit]
    if not rows:
        return "_нет данных_\n"
    cols = [c for c in cols if c in rows[0]]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")).replace("|", "/")[:60] for c in cols) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30, help="период статистики, дней (по вчера включительно)")
    ap.add_argument("--out", default="direct_audit_data", help="папка для результатов")
    ap.add_argument("--client-login", help="логин клиента (для агентских аккаунтов)")
    ap.add_argument("--sandbox", action="store_true", help="работать с песочницей")
    ap.add_argument("--include-archived", action="store_true", help="включать архивные кампании")
    args = ap.parse_args()

    api = Direct(load_token(), SANDBOX if args.sandbox else PROD, args.client_login)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    d_to = date.today() - timedelta(days=1)
    d_from = d_to - timedelta(days=args.days - 1)
    d_from_s, d_to_s = d_from.isoformat(), d_to.isoformat()
    problems: list[str] = []

    def save(name: str, data) -> None:
        (out / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    # --- структура ---
    print("Кампании…")
    sub = ["BiddingStrategy", "CounterIds", "PriorityGoals", "Settings"]
    try:
        campaigns = api.get_robust(
            "campaigns",
            "Campaigns",
            {
                "SelectionCriteria": {},
                "FieldNames": ["Id", "Name", "Type", "State", "Status", "StatusClarification", "StartDate",
                               "DailyBudget", "NegativeKeywords", "Statistics"],
                "TextCampaignFieldNames": sub,
                "UnifiedCampaignFieldNames": sub,
            },
        )
    except ApiError as e:
        sys.exit(f"Не удалось получить кампании: {e}\n"
                 "Код 58 — заявка на доступ ещё не одобрена; 53 — неверный/просроченный токен; "
                 "8800/54 — нет прав на аккаунт (проверьте --client-login).")
    save("campaigns", campaigns)
    active = [c for c in campaigns if args.include_archived or c.get("State") != "ARCHIVED"]
    ids = [c["Id"] for c in active]
    print(f"  всего {len(campaigns)}, в работе для выгрузки {len(ids)}")

    print("Группы…")
    adgroups = by_campaigns(api, "adgroups", "AdGroups", ids, {
        "FieldNames": ["Id", "Name", "CampaignId", "Type", "Status", "ServingStatus", "RegionIds", "NegativeKeywords"],
    })
    save("adgroups", adgroups)

    print("Объявления…")
    ads = by_campaigns(api, "ads", "Ads", ids, {
        "FieldNames": ["Id", "AdGroupId", "CampaignId", "Type", "State", "Status", "StatusClarification"],
        "TextAdFieldNames": ["Title", "Title2", "Text", "Href", "DisplayUrlPath", "SitelinkSetId",
                             "AdExtensions", "VCardId", "AdImageHash"],
    })
    save("ads", ads)

    print("Ключевые фразы…")
    keywords = by_campaigns(api, "keywords", "Keywords", ids, {
        "FieldNames": ["Id", "Keyword", "AdGroupId", "CampaignId", "State", "Status", "ServingStatus", "Bid", "ContextBid"],
    })
    save("keywords", keywords)

    # --- отчёты ---
    reports: dict[str, list[dict]] = {}
    for name, (rtype, fields) in REPORTS.items():
        print(f"Отчёт {name} ({d_from_s} — {d_to_s})…")
        try:
            text = api.report(name, rtype, fields, d_from_s, d_to_s)
        except ApiError as e:
            warn(f"{e}. Повторяю без конверсий")
            try:
                text = api.report(name, rtype, [f for f in fields if f not in CONV], d_from_s, d_to_s)
                problems.append(f"Отчёт {name}: без конверсий ({e})")
            except ApiError as e2:
                warn(str(e2))
                problems.append(f"Отчёт {name} не выгружен: {e2}")
                continue
        (out / f"report_{name}.tsv").write_text(text, encoding="utf-8")
        reports[name] = read_tsv(text)

    # --- сводка ---
    camp = reports.get("campaigns", [])
    cost = sum(num(r.get("Cost")) for r in camp)
    clicks = sum(num(r.get("Clicks")) for r in camp)
    shows = sum(num(r.get("Impressions")) for r in camp)
    conv = sum(num(r.get("Conversions")) for r in camp)
    has_conv = bool(camp) and "Conversions" in camp[0]

    def section(title: str, rows: list[dict], cols_first: list[str], n: int = 25) -> list[str]:
        """Топ по расходу; если в отчёте есть конверсии — только строки без конверсий."""
        zero = bool(rows) and "Conversions" in rows[0]
        if zero:
            rows = [r for r in rows if num(r.get("Conversions")) == 0]
        rows = sorted(rows, key=lambda r: num(r.get("Cost")), reverse=True)
        return [f"## {title}: топ по расходу{' без конверсий' if zero else ''}\n", table(rows, cols_first + cols, n)]

    text_ads = [a for a in ads if a.get("TextAd")]
    no_sitelinks = sum(1 for a in text_ads if not a["TextAd"].get("SitelinkSetId"))
    no_ext = sum(1 for a in text_ads if not a["TextAd"].get("AdExtensions"))
    no_title2 = sum(1 for a in text_ads if not a["TextAd"].get("Title2"))
    no_utm = sum(1 for a in text_ads if "utm_" not in (a["TextAd"].get("Href") or ""))
    ads_per_group: dict = {}
    for a in ads:
        if a.get("State") != "ARCHIVED":
            ads_per_group[a["AdGroupId"]] = ads_per_group.get(a["AdGroupId"], 0) + 1
    single_ad_groups = sum(1 for v in ads_per_group.values() if v == 1)
    with_settings = [c for c in active if c.get("TextCampaign") or c.get("UnifiedCampaign")]
    no_counter = [c["Name"] for c in with_settings
                  if not ((c.get("TextCampaign") or c.get("UnifiedCampaign")).get("CounterIds") or {}).get("Items")]
    rejected = sum(1 for a in ads if a.get("Status") == "REJECTED")

    sq = reports.get("search_queries", [])
    pl = [r for r in reports.get("placements", []) if r.get("AdNetworkType") == "AD_NETWORK"]
    cols = ["Cost", "Clicks", "Ctr", "BounceRate", "Conversions", "CostPerConversion"]

    md = [
        f"# Сводка выгрузки Директа ({d_from_s} — {d_to_s})\n",
        f"Аккаунт: {args.client_login or 'текущий логин'}{' (песочница)' if args.sandbox else ''}. "
        "Деньги в отчётах — в валюте аккаунта с НДС; суммы в campaigns.json (DailyBudget, Bid) — в микроединицах (÷ 1 000 000).\n",
        "## Итог\n",
        f"- Расход: {cost:,.0f}".replace(",", " "),
        f"- Показы: {shows:,.0f} · Клики: {clicks:,.0f} · CTR: {(clicks / shows * 100 if shows else 0):.2f}% · "
        f"CPC: {(cost / clicks if clicks else 0):.2f}".replace(",", " "),
        (f"- Конверсии: {conv:,.0f} · CPA: {(cost / conv if conv else 0):,.0f}".replace(",", " ")
         if has_conv else "- Конверсии: нет данных (нет целей Метрики или отчёт без конверсий)"),
        f"- Кампаний: {len(campaigns)} (выгружено {len(ids)}), групп: {len(adgroups)}, объявлений: {len(ads)}, фраз: {len(keywords)}",
        "",
        "## Быстрые сигналы\n",
        (f"- Кампании без счётчика Метрики: {len(no_counter)}" + (f" — {', '.join(no_counter[:10])}" if no_counter else "")
         if with_settings else "- Счётчики Метрики: нет данных (настройки кампаний не выгрузились)"),
        f"- Групп с одним объявлением: {single_ad_groups} из {len(ads_per_group)}",
        f"- Текстовых объявлений без быстрых ссылок: {no_sitelinks} из {len(text_ads)}; без уточнений/расширений: {no_ext}; "
        f"без второго заголовка: {no_title2}; без UTM: {no_utm}",
        f"- Отклонённых объявлений: {rejected}",
        "",
        "## Кампании по расходу\n",
        table(sorted(camp, key=lambda r: num(r.get("Cost")), reverse=True), ["CampaignName", "CampaignType"] + cols, 20),
        *section("Поисковые запросы", sq, ["Query", "CampaignName"]),
        *section("Площадки РСЯ", pl, ["Placement", "CampaignName"]),
        *section("Ключевые фразы", reports.get("criteria", []), ["Criterion", "CampaignName", "AdNetworkType"]),
    ]
    if problems:
        md += ["## Что не выгрузилось\n"] + [f"- {p}" for p in problems]
    (out / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nГотово: {out}/summary.md  (баллы API: {api.units or 'н/д'})")


if __name__ == "__main__":
    main()
