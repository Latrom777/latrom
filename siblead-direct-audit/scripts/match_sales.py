#!/usr/bin/env python3
"""Сопоставление продаж с кампаниями Директа по UTM: выручка, ROMI, ДРР, CAC по кампаниям.

Продажи — любая выгрузка (GetCourse, AmoCRM, Bitrix24, Prodamus, Тильда, Google Таблица, CSV/XLSX).
Колонки определяются автоматически: UTM-метки (отдельными колонками или внутри ссылки),
сумма, статус оплаты, количество (если таблица уже сгруппирована). Контакты (email, телефон, имя)
в результаты не попадают.

Расходы — report_campaigns.tsv из direct_fetch.py или выгрузка «Статистика по кампаниям» из Директа.

  python match_sales.py --sales orders.xlsx --costs report_campaigns.tsv --out sales_match
  python match_sales.py --sales "https://docs.google.com/spreadsheets/d/<id>/edit#gid=0" --costs campaigns.xlsx
  # если автоопределение ошиблось:
  python match_sales.py ... --amount-col "Оплачено" --status-col "Статус" --paid-status "Завершен,Оплачен"
"""

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd

UTM = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"]
SOURCE_YANDEX = r"yandex|яндекс|^ya$|^ya[_\-.]|direct|директ|ydirect|yd$"
PAID = r"оплач|заверш|выполн|успешн|реализ|paid|success|complete|оплата получена|продажа"
NOT_PAID = r"(^|\s)не\s|не реализ|закрыт|lost|не оплач|неоплач|отмен|отказ|возврат|cancel|refund|unpaid|ожида|частично"
PII = r"e-?mail|почт|телефон|phone|тел\.|фио|^имя|name$|клиент|contact|контакт|пользовател|user"

AMOUNT_PRI = [r"оплачено|получено|выручк|revenue|paid amount|сумма оплат", r"сумм|стоимост|amount|price|цена|бюджет|итого|total"]
COLS = {
    "status": r"статус|status|этап|stage",
    "count": r"количеств|кол-во|число заказов|заказов|orders|продаж$|sales$|оплат$",
    "date": r"дата|date|создан|оплачен в",
    "url": r"url|ссылк|landing|страниц|referer|реферер|адрес|source page|utm$|метки",
}
COST_COLS = {
    "id": r"campaignid|№ кампании|id кампании|номер кампании|кампания №|campaign id|^id$",
    "name": r"campaignname|^кампания$|название кампании|campaign$|campaign name|наименование",
    "cost": r"^cost$|расход|затраты|стоимость|spend",
}


def warn(msg: str) -> None:
    print(f"  ! {msg}", file=sys.stderr)


def num(v) -> float:
    if pd.isna(v):
        return 0.0
    s = re.sub(r"[^\d,.\-]", "", str(v).replace("\xa0", "").replace(" ", ""))
    if s.count(",") == 1 and s.count(".") == 0:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def gsheet_csv(url: str) -> str:
    m = re.search(r"/spreadsheets/d/([\w-]+)", url)
    gid = re.search(r"[#&?]gid=(\d+)", url)
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv" + (f"&gid={gid.group(1)}" if gid else "")


def read_any(src: str, must: str) -> pd.DataFrame:
    """Читает csv/tsv/xlsx/Google Sheets; находит строку заголовков (по ключевому слову must)."""
    if src.startswith("http"):
        if "docs.google.com/spreadsheets" in src:
            src = gsheet_csv(src)
        raw = pd.read_csv(src, header=None, dtype=str)
    elif src.lower().endswith((".xlsx", ".xls", ".xlsm")):
        sheets = pd.read_excel(src, header=None, dtype=str, sheet_name=None)
        raw = max(sheets.values(), key=len)  # самый большой лист
    else:
        text = Path(src).read_bytes()
        enc = "utf-8-sig"
        try:
            text.decode(enc)
        except UnicodeDecodeError:
            enc = "cp1251"
        sample = text[:5000].decode(enc, errors="ignore")
        sep = max(["\t", ";", ","], key=sample.count)
        raw = pd.read_csv(src, header=None, dtype=str, sep=sep, encoding=enc, engine="python", on_bad_lines="skip")
    header_row = 0
    for i in range(min(30, len(raw))):
        cells = [str(x).lower() for x in raw.iloc[i].tolist() if str(x).strip() and str(x).lower() != "nan"]
        if len(cells) >= 2 and re.search(must, " | ".join(cells)):
            header_row = i
            break
    df = raw.iloc[header_row + 1 :].copy()
    df.columns = [str(c).strip() for c in raw.iloc[header_row].tolist()]
    df = df.loc[:, [c for c in df.columns if c and c.lower() != "nan"]]
    df = df.dropna(how="all")
    # строка «Итого» внизу
    first = df.columns[0]
    df = df[~df[first].astype(str).str.lower().str.contains(r"^итого|^всего|^total", regex=True, na=False)]
    return df.reset_index(drop=True)


def md(df: pd.DataFrame) -> str:
    def fmt(v) -> str:
        if pd.isna(v):
            return ""
        if isinstance(v, float):
            return f"{v:,.0f}".replace(",", " ") if abs(v) >= 100 or v == int(v) else f"{v:.1f}"
        return str(v)

    rows = [[fmt(v) for v in r] for r in df.itertuples(index=False)]
    head = "| " + " | ".join(map(str, df.columns)) + " |\n|" + "---|" * len(df.columns)
    return head + "".join("\n| " + " | ".join(r) + " |" for r in rows)


def find(cols, pattern: str, exclude=()):
    for c in cols:
        lc = c.lower()
        if c not in exclude and re.search(pattern, lc):
            return c
    return None


TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                     "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def norm(s: str) -> str:
    s = unquote(str(s)).lower().replace("+", " ")
    s = "".join(TRANSLIT.get(ch, ch) for ch in s)
    return re.sub(r"[^a-z0-9]+", "", s)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sales", required=True, help="файл или ссылка с продажами")
    ap.add_argument("--costs", help="расходы по кампаниям (report_campaigns.tsv или выгрузка из Директа)")
    ap.add_argument("--out", default="sales_match")
    ap.add_argument("--amount-col"), ap.add_argument("--status-col"), ap.add_argument("--count-col")
    ap.add_argument("--paid-status", help="значения статуса «оплачено» через запятую (по умолчанию — автоопределение)")
    ap.add_argument("--all-sources", action="store_true", help="не фильтровать utm_source по Яндексу")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = []

    # ---------- продажи ----------
    sales = read_any(args.sales, r"utm|сумм|amount|статус|status|оплач")
    cols = list(sales.columns)
    pii = [c for c in cols if re.search(PII, c.lower()) and not re.search(r"utm|кампан|campaign", c.lower())]
    sales = sales.drop(columns=pii)
    cols = list(sales.columns)

    utm_cols = {u: find(cols, u.replace("_", r"[_ \-]?")) for u in UTM}
    url_col = find(cols, COLS["url"], exclude=[c for c in utm_cols.values() if c])
    # UTM внутри ссылки / строки с метками
    utm = pd.DataFrame(index=sales.index)
    for u in UTM:
        utm[u] = sales[utm_cols[u]].astype(str) if utm_cols[u] else ""
    utm = utm.replace({"nan": "", "None": ""})
    scan_cols = [c for c in cols if sales[c].astype(str).str.contains("utm_", na=False).any()]
    for c in scan_cols:
        for i, val in sales[c].astype(str).items():
            if "utm_" not in val:
                continue
            q = urlparse(val).query or val.split("?", 1)[-1]
            params = parse_qs(q)
            for u in UTM:
                if not utm.at[i, u] and params.get(u):
                    utm.at[i, u] = params[u][0]

    amount_col = args.amount_col
    if not amount_col:
        for pat in AMOUNT_PRI:
            amount_col = find(cols, pat, exclude=list(utm_cols.values()))
            if amount_col:
                break
    if not amount_col:
        sys.exit(f"Не нашёл колонку с суммой. Колонки: {cols}. Укажите --amount-col")
    status_col = args.status_col or find(cols, COLS["status"])
    count_col = args.count_col or find(cols, COLS["count"], exclude=[amount_col])

    df = pd.DataFrame({
        "amount": sales[amount_col].map(num),
        "count": sales[count_col].map(num) if count_col else 1.0,
        **{u: utm[u].fillna("").astype(str).str.strip() for u in UTM},
    })
    report.append(f"- Строк в файле продаж: {len(df)}")
    report.append(f"- Колонки: сумма — «{amount_col}»; статус — «{status_col or 'нет'}»; количество — «{count_col or 'нет (1 строка = 1 продажа)'}»; "
                  f"UTM — {', '.join(f'{u}: «{c}»' for u, c in utm_cols.items() if c) or 'отдельных колонок нет'}"
                  + (f"; UTM найдены внутри колонок: {', '.join(scan_cols)}" if scan_cols else ""))
    if pii:
        report.append(f"- Удалены колонки с контактами: {', '.join(pii)}")

    if status_col:
        st = sales[status_col].astype(str).str.strip()
        counts = st.value_counts().head(15)
        report.append("- Статусы в файле: " + "; ".join(f"«{k}» — {v}" for k, v in counts.items()))
        if args.paid_status:
            allowed = {s.strip().lower() for s in args.paid_status.split(",")}
            mask = st.str.lower().isin(allowed)
        else:
            low = st.str.lower()
            mask = low.str.contains(PAID, regex=True) & ~low.str.contains(NOT_PAID, regex=True)
        if mask.any():
            report.append(f"- Считаю оплаченными: {', '.join(sorted(st[mask].unique())[:10])} ({int(mask.sum())} строк)")
            df = df[mask.values]
        else:
            warn("не распознал статус «оплачено» — считаю все строки; уточните --paid-status")
            report.append("- ⚠ Статус «оплачено» не распознан, учтены все строки — проверьте и укажите --paid-status")
    df = df[df["amount"] > 0]

    total_rev, total_orders = df["amount"].sum(), df["count"].sum()
    has_src = (df["utm_source"] != "").any()
    if has_src and not args.all_sources:
        ya = df["utm_source"].str.lower().str.contains(SOURCE_YANDEX, regex=True)
        report.append(f"- Оплаченных продаж всего: {total_orders:,.0f} на {total_rev:,.0f} ₽; из них utm_source = Яндекс: "
                      f"{df[ya]['count'].sum():,.0f} на {df[ya]['amount'].sum():,.0f} ₽".replace(",", " "))
        other = df[~ya].groupby("utm_source")["amount"].sum().sort_values(ascending=False).head(8)
        if len(other):
            report.append("- Другие источники (не учитываются): " + "; ".join(f"{k or '(без метки)'} — {v:,.0f} ₽".replace(",", " ") for k, v in other.items()))
        df = df[ya]
    else:
        report.append(f"- Оплаченных продаж: {total_orders:,.0f} на {total_rev:,.0f} ₽ (фильтр по utm_source не применялся)".replace(",", " "))

    # ---------- расходы и сопоставление ----------
    camp = pd.DataFrame(columns=["id", "name", "cost"])
    if args.costs:
        c = read_any(args.costs, r"расход|cost|кампан|campaign")
        cid = find(c.columns, COST_COLS["id"])
        cname = find(c.columns, COST_COLS["name"], exclude=[cid])
        ccost = find(c.columns, COST_COLS["cost"])
        if not ccost or not (cid or cname):
            warn(f"в файле расходов не нашёл кампанию/расход. Колонки: {list(c.columns)}")
        else:
            camp = pd.DataFrame({
                "id": c[cid].astype(str).str.extract(r"(\d+)")[0] if cid else "",
                "name": c[cname].astype(str) if cname else "",
                "cost": c[ccost].map(num),
            })
            camp = camp.groupby(["id", "name"], as_index=False, dropna=False)["cost"].sum()
    ids = {str(i): i for i in camp["id"].dropna() if str(i) not in ("", "nan")}
    names = {norm(n): (i, n) for i, n in zip(camp["id"], camp["name"]) if norm(n)}

    def match(v: str):
        if not v:
            return None
        for d in re.findall(r"\d{5,12}", v):
            if d in ids:
                return d
        nv = norm(v)
        if nv in names:
            return names[nv][0]
        hits = [i for k, (i, _) in names.items() if len(nv) >= 4 and (nv in k or k in nv)]
        return hits[0] if len(set(hits)) == 1 else None

    df["campaign_id"] = df["utm_campaign"].map(match) if len(camp) else None
    matched = df[df["campaign_id"].notna()]
    rev_ya = df["amount"].sum()
    rate = matched["amount"].sum() / rev_ya * 100 if rev_ya else 0
    no_utm = df[df["utm_campaign"] == ""]["amount"].sum()
    report.append(f"- Сопоставлено с кампаниями: {rate:.0f}% выручки Яндекса" + (f"; без utm_campaign: {no_utm:,.0f} ₽".replace(",", " ") if no_utm else ""))

    df["count"] = df["count"].astype(float)
    by = matched.groupby("campaign_id").agg(orders=("count", "sum"), revenue=("amount", "sum")).reset_index()
    res = camp.merge(by, left_on="id", right_on="campaign_id", how="left").fillna({"orders": 0, "revenue": 0})
    res["orders"] = res["orders"].astype(int)
    res["ROMI_%"] = ((res["revenue"] - res["cost"]) / res["cost"] * 100).where(res["cost"] > 0).round(0) + 0.0
    res["ДРР_%"] = (res["cost"] / res["revenue"] * 100).where(res["revenue"] > 0).round(1)
    res["CAC"] = (res["cost"] / res["orders"]).where(res["orders"] > 0).round(0)
    res = res[["id", "name", "cost", "orders", "revenue", "ROMI_%", "ДРР_%", "CAC"]].sort_values("cost", ascending=False)
    res.to_csv(out / "sales_by_campaign.tsv", sep="\t", index=False)

    unmatched = (df[df["campaign_id"].isna()].groupby("utm_campaign").agg(orders=("count", "sum"), revenue=("amount", "sum"))
                 .sort_values("revenue", ascending=False).reset_index())
    unmatched["orders"] = unmatched["orders"].astype(int)
    unmatched.to_csv(out / "unmatched_utm_campaign.tsv", sep="\t", index=False)
    for u, fname in (("utm_term", "sales_by_utm_term.tsv"), ("utm_content", "sales_by_utm_content.tsv")):
        if (df[u] != "").any():
            (df.groupby(u).agg(orders=("count", "sum"), revenue=("amount", "sum")).sort_values("revenue", ascending=False)
             .reset_index().to_csv(out / fname, sep="\t", index=False))

    cost = res["cost"].sum()
    rev = res["revenue"].sum()
    lines = ["# Продажи × Директ\n", *report, ""]
    if cost:
        lines += [f"**Итого по кампаниям из файла расходов:** расход {cost:,.0f} ₽ · продаж {res['orders'].sum():,.0f} · выручка {rev:,.0f} ₽ · "
                  f"ROMI {((rev - cost) / cost * 100):.0f}% · ДРР {(cost / rev * 100 if rev else 0):.1f}%".replace(",", " "),
                  "", "ROMI и ДРР считаются по выручке, без учёта маржи, налогов и комиссий платформ.", "",
                  md(res.head(30))]
    if len(unmatched):
        lines += ["", "## Не сопоставлено (utm_campaign → выручка)", "", md(unmatched.head(20))]
    text = "\n".join(lines)
    (out / "sales_summary.md").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
