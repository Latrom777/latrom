#!/usr/bin/env python3
"""Воронка онлайн-школы × кампании Директа по UTM.

Шаги воронки: регистрации → дошли до вебинара → заявки → продажи. На каждый шаг — свой файл
(любой можно пропустить). Файлы — выгрузки GetCourse, Bizon365, AmoCRM, Bitrix24, Prodamus,
Тильды, Google Таблица (ссылка), CSV/XLSX, или уже сгруппированная таблица «utm_campaign + количество».

Для каждого шага: UTM-метки ищутся в отдельных колонках или внутри ссылок; дубли людей
(по email/телефону) схлопываются; контакты удаляются и в результаты не попадают.
Для продаж дополнительно: сумма и статус «оплачено».

  python funnel.py --costs campaigns.xlsx --regs regs.csv --attended web.csv --apps deals.xlsx --sales orders.xlsx
  python funnel.py --costs report_campaigns.tsv --sales "https://docs.google.com/spreadsheets/d/<id>/edit#gid=0"
  # уточнения, если автоопределение ошиблось:
  #   --amount-col "Оплачено" --status-col "Статус" --paid-status "Завершен,Оплачен" --days 30
"""

import argparse
import hashlib
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd

UTM = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"]
STAGES = [("regs", "Регистрации"), ("attended", "Дошли до вебинара"), ("apps", "Заявки"), ("sales", "Продажи")]
SOURCE_YANDEX = r"yandex|яндекс|^ya$|^ya[_\-.]|direct|директ|ydirect|yd$"
PAID = r"оплач|заверш|выполн|успешн|реализ|paid|success|complete|оплата получена|продажа"
NOT_PAID = r"(?:^|\s)не\s|не реализ|закрыт|lost|не оплач|неоплач|отмен|отказ|возврат|cancel|refund|unpaid|ожида|частично"
PII = r"e-?mail|почт|телефон|phone|тел\.|фио|^имя|name$|клиент|contact|контакт|пользовател|user"
PII_KEY = r"e-?mail|почт|телефон|phone"
AMOUNT_PRI = [r"оплачено|получено|выручк|revenue|paid amount|сумма оплат", r"сумм|стоимост|amount|price|цена|бюджет|итого|total"]
STATUS = r"статус|status|этап|stage"
COUNT = r"количеств|кол-во|число|заказов|orders|продаж$|sales$|оплат$|регистрац|зрител|заявок|^leads$|^users$"
COST_COLS = {
    "id": r"campaignid|№ кампании|id кампании|номер кампании|кампания №|campaign id|^id$",
    "name": r"campaignname|^кампания$|название кампании|campaign$|campaign name|наименование",
    "cost": r"^cost$|расход|затраты|стоимость|spend",
}
TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                     "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def warn(msg: str) -> None:
    print(f"  ! {msg}", file=sys.stderr)


def num(v) -> float:
    if pd.isna(v):
        return 0.0
    s = re.sub(r"[^\d,.\-]", "", str(v).replace("\xa0", "").replace(" ", ""))
    s = s.replace(",", ".") if s.count(",") == 1 and s.count(".") == 0 else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def norm(s: str) -> str:
    s = unquote(str(s)).lower().replace("+", " ")
    return re.sub(r"[^a-z0-9]+", "", "".join(TRANSLIT.get(ch, ch) for ch in s))


def find(cols, pattern: str, exclude=()):
    for c in cols:
        if c not in exclude and re.search(pattern, c.lower()):
            return c
    return None


def gsheet_csv(url: str) -> str:
    m = re.search(r"/spreadsheets/d/([\w-]+)", url)
    gid = re.search(r"[#&?]gid=(\d+)", url)
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv" + (f"&gid={gid.group(1)}" if gid else "")


def read_any(src: str, must: str) -> pd.DataFrame:
    """csv/tsv/xlsx/Google Sheets; строка заголовков — первая с ≥2 ячейками и ключевым словом."""
    if src.startswith("http"):
        raw = pd.read_csv(gsheet_csv(src) if "docs.google.com/spreadsheets" in src else src, header=None, dtype=str)
    elif src.lower().endswith((".xlsx", ".xls", ".xlsm")):
        raw = max(pd.read_excel(src, header=None, dtype=str, sheet_name=None).values(), key=len)
    else:
        data = Path(src).read_bytes()
        enc = "utf-8-sig"
        try:
            data.decode(enc)
        except UnicodeDecodeError:
            enc = "cp1251"
        sample = data[:5000].decode(enc, errors="ignore")
        raw = pd.read_csv(src, header=None, dtype=str, sep=max(["\t", ";", ","], key=sample.count),
                          encoding=enc, engine="python", on_bad_lines="skip")
    header = 0
    for i in range(min(30, len(raw))):
        cells = [str(x).lower() for x in raw.iloc[i].tolist() if str(x).strip() and str(x).lower() != "nan"]
        if len(cells) >= 2 and re.search(must, " | ".join(cells)):
            header = i
            break
    df = raw.iloc[header + 1:].copy()
    df.columns = [str(c).strip() for c in raw.iloc[header].tolist()]
    df = df.loc[:, [c for c in df.columns if c and c.lower() != "nan"]].dropna(how="all")
    first = df.columns[0]
    df = df[~df[first].astype(str).str.lower().str.contains(r"^итого|^всего|^total", regex=True, na=False)]
    return df.reset_index(drop=True)


def load_stage(src: str, sales: bool, args, notes: list) -> pd.DataFrame:
    """→ DataFrame[count, amount, utm_*] для одного шага воронки."""
    d = read_any(src, r"utm|сумм|amount|статус|status|оплач|email|почт|телефон|дата|date|кампан|campaign")
    cols = list(d.columns)
    utm_cols = {u: find(cols, u.replace("_", r"[_ \-]?")) for u in UTM}
    utm = pd.DataFrame({u: (d[c].astype(str) if c else "") for u, c in utm_cols.items()}, index=d.index)
    utm = utm.replace({"nan": "", "None": ""})
    scan = [c for c in cols if d[c].astype(str).str.contains("utm_", na=False).any()]
    for c in scan:
        for i, val in d[c].astype(str).items():
            if "utm_" in val:
                params = parse_qs(urlparse(val).query or val.split("?", 1)[-1])
                for u in UTM:
                    if not utm.at[i, u] and params.get(u):
                        utm.at[i, u] = params[u][0]

    key_cols = [c for c in cols if re.search(PII_KEY, c.lower())]
    count_col = None
    if not key_cols:  # количество бывает только в сгруппированной таблице, где нет контактов
        for c in cols:
            if c in utm_cols.values() or not re.search(COUNT, c.lower()) or re.search(r"дата|date|ссылк|url|время|time", c.lower()):
                continue
            vals = d[c].dropna().astype(str).str.replace(r"[\s\xa0]", "", regex=True)
            if len(vals) and vals.str.fullmatch(r"\d+([.,]\d+)?").mean() > 0.8:
                count_col = c
                break
    amount_col = status_col = None
    if sales:
        amount_col = args.amount_col
        if not amount_col:
            for pat in AMOUNT_PRI:
                if (amount_col := find(cols, pat, exclude=[c for c in utm_cols.values() if c] + [count_col])):
                    break
        if not amount_col:
            sys.exit(f"Продажи: не нашёл колонку с суммой. Колонки: {cols}. Укажите --amount-col")
        status_col = args.status_col or find(cols, STATUS)
    if count_col and re.search(PII, count_col.lower()):
        count_col = None

    out = pd.DataFrame({
        "count": d[count_col].map(num) if count_col else 1.0,
        "amount": d[amount_col].map(num) if amount_col else 0.0,
        **{u: utm[u].fillna("").astype(str).str.strip() for u in UTM},
    })

    # дубли людей: по email/телефону (хэш), до удаления контактов
    dup_note = ""
    if key_cols and not count_col:
        key = (d[key_cols].fillna("").astype(object).map(lambda x: re.sub(r"\s", "", str(x)).lower().replace("nan", ""))
               .agg("|".join, axis=1))
        out["_k"] = key.map(lambda s: hashlib.sha1(s.encode()).hexdigest() if s.strip("|") else None)
        before = len(out)
        if not sales:
            out = pd.concat([out[out["_k"].isna()], out[out["_k"].notna()].drop_duplicates("_k")])
        dup_note = f"; дублей по контактам убрано: {before - len(out)}" if before - len(out) else ""
        out = out.drop(columns="_k")
    pii = [c for c in cols if re.search(PII, c.lower()) and not re.search(r"utm|кампан|campaign", c.lower())]

    note = (f"строк {len(d)}{dup_note}; UTM: "
            + (", ".join(f"{u}=«{c}»" for u, c in utm_cols.items() if c) or "отдельных колонок нет")
            + (f"; UTM внутри колонок: {', '.join(scan)}" if scan else "")
            + (f"; количество — «{count_col}»" if count_col else "")
            + (f"; сумма — «{amount_col}»; статус — «{status_col or 'нет'}»" if sales else "")
            + (f"; удалены контакты: {', '.join(pii)}" if pii else ""))

    if sales and status_col:
        st = d.loc[out.index, status_col].astype(str).str.strip()
        note += "; статусы: " + ", ".join(f"«{k}» {v}" for k, v in st.value_counts().head(12).items())
        if args.paid_status:
            mask = st.str.lower().isin({s.strip().lower() for s in args.paid_status.split(",")})
        else:
            low = st.str.lower()
            mask = low.str.contains(PAID, regex=True) & ~low.str.contains(NOT_PAID, regex=True)
        if mask.any():
            note += f"; оплачено = {', '.join(sorted(st[mask].unique())[:8])}"
            out = out[mask.values]
        else:
            note += "; ⚠ статус «оплачено» не распознан — учтены все строки, уточните --paid-status"
    if sales:
        out = out[out["amount"] > 0]

    if (out["utm_source"] != "").any() and not args.all_sources:
        ya = out["utm_source"].str.lower().str.contains(SOURCE_YANDEX, regex=True)
        note += f"; Яндекс: {out[ya]['count'].sum():,.0f} из {out['count'].sum():,.0f}".replace(",", " ")
        out = out[ya]
    else:
        note += "; utm_source нет — фильтр по Яндексу не применялся"
    notes.append(note)
    return out


def pct(a, b):
    return round(a / b * 100, 1) if b else None


def money(a, b):
    return round(a / b) if b else None


def md(df: pd.DataFrame) -> str:
    def fmt(v) -> str:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        if isinstance(v, float):
            return f"{v:,.0f}".replace(",", " ") if abs(v) >= 100 or v == int(v) else f"{v:.1f}"
        if isinstance(v, int):
            return f"{v:,}".replace(",", " ")
        return str(v)
    head = "| " + " | ".join(map(str, df.columns)) + " |\n|" + "---|" * len(df.columns)
    return head + "".join("\n| " + " | ".join(fmt(v) for v in r) + " |" for r in df.itertuples(index=False))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--costs", help="расходы по кампаниям (report_campaigns.tsv или выгрузка из Директа)")
    for key, title in STAGES:
        ap.add_argument(f"--{key}", help=f"{title}: файл или ссылка")
    ap.add_argument("--days", type=float, help="длина рекламного периода в днях (для расчёта бюджета в месяц)")
    ap.add_argument("--out", default="funnel_out")
    ap.add_argument("--amount-col"), ap.add_argument("--status-col")
    ap.add_argument("--paid-status", help="значения статуса «оплачено» через запятую")
    ap.add_argument("--all-sources", action="store_true", help="не фильтровать utm_source по Яндексу")
    args = ap.parse_args()
    if not any(getattr(args, k) for k, _ in STAGES):
        ap.error("нужен хотя бы один шаг воронки: --regs / --attended / --apps / --sales")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# Воронка × Директ\n", "## Что распознано\n"]

    # --- расходы ---
    camp = pd.DataFrame(columns=["id", "name", "cost"])
    if args.costs:
        c = read_any(args.costs, r"расход|cost|кампан|campaign")
        cid, ccost = find(c.columns, COST_COLS["id"]), find(c.columns, COST_COLS["cost"])
        cname = find(c.columns, COST_COLS["name"], exclude=[cid])
        if ccost and (cid or cname):
            camp = pd.DataFrame({"id": c[cid].astype(str).str.extract(r"(\d+)")[0] if cid else "",
                                 "name": c[cname].astype(str) if cname else "", "cost": c[ccost].map(num)})
            camp = camp.groupby(["id", "name"], as_index=False, dropna=False)["cost"].sum()
            lines.append(f"- Расходы: кампаний {len(camp)} · всего {camp['cost'].sum():,.0f} ₽".replace(",", " "))
        else:
            warn(f"в файле расходов не нашёл кампанию/расход. Колонки: {list(c.columns)}")
    ids = {str(i) for i in camp["id"].dropna() if str(i) not in ("", "nan")}
    names = {norm(n): i for i, n in zip(camp["id"], camp["name"]) if norm(n)}

    def match(v: str):
        if not v:
            return None
        for d in re.findall(r"\d{5,12}", v):
            if d in ids:
                return d
        nv = norm(v)
        if nv in names:
            return names[nv]
        hits = {i for k, i in names.items() if len(nv) >= 4 and (nv in k or k in nv)}
        return hits.pop() if len(hits) == 1 else None

    # --- шаги ---
    agg = camp.set_index("id")[["name", "cost"]].copy() if len(camp) else pd.DataFrame(columns=["name", "cost"])
    totals, unmatched_all = {}, []
    for key, title in STAGES:
        src = getattr(args, key)
        if not src:
            continue
        notes = []
        st = load_stage(src, key == "sales", args, notes)
        st["campaign_id"] = st["utm_campaign"].map(match) if len(camp) else None
        tot = st["count"].sum()
        m = st[st["campaign_id"].notna()]
        totals[key] = {"count": tot, "amount": st["amount"].sum(), "matched": m["count"].sum(),
                       "has_utm": (st["utm_campaign"] != "").any()}
        lines.append(f"- **{title}**: {notes[0]}; сопоставлено с кампаниями: {pct(m['count'].sum(), tot) or 0}%")
        g = m.groupby("campaign_id").agg(**{key: ("count", "sum")}, **({"revenue": ("amount", "sum")} if key == "sales" else {}))
        agg = agg.join(g, how="left")
        um = st[st["campaign_id"].isna()].groupby("utm_campaign")["count"].sum()
        for k, v in um.items():
            unmatched_all.append({"шаг": title, "utm_campaign": k or "(пусто)", "количество": int(v)})
        if key == "sales":
            for u, fname in (("utm_term", "sales_by_utm_term.tsv"), ("utm_content", "sales_by_utm_content.tsv")):
                if (st[u] != "").any():
                    (st.groupby(u).agg(orders=("count", "sum"), revenue=("amount", "sum"))
                     .sort_values("revenue", ascending=False).reset_index().to_csv(out_dir / fname, sep="\t", index=False))

    # --- таблица по кампаниям ---
    have = [k for k, _ in STAGES if k in totals]
    if len(agg):
        agg = agg.fillna({k: 0 for k in have + (["revenue"] if "sales" in have else [])})
        rows = []
        for cid, r in agg.sort_values("cost", ascending=False).iterrows():
            row = {"id": cid, "кампания": r["name"], "расход": r["cost"]}
            prev = None
            for k, title in STAGES:
                if k not in have:
                    continue
                n = int(r[k])
                row[title.split()[0].lower()] = n
                row[f"₽/{title.split()[0].lower()[:4]}"] = money(r["cost"], n)
                if prev is not None:
                    row[f"%{prev[1][:4]}→{title.split()[0].lower()[:4]}"] = pct(n, prev[0])
                prev = (n, title.split()[0].lower())
            if "sales" in have:
                row["выручка"] = r["revenue"]
                row["ROMI_%"] = round((r["revenue"] - r["cost"]) / r["cost"] * 100) if r["cost"] else None
                row["ДРР_%"] = pct(r["cost"], r["revenue"])
            rows.append(row)
        table = pd.DataFrame(rows)
        table.to_csv(out_dir / "funnel_by_campaign.tsv", sep="\t", index=False)

    # --- итог воронки ---
    cost = float(camp["cost"].sum()) if len(camp) else 0.0
    lines += ["", "## Воронка в целом (трафик Яндекса)\n", "| Шаг | Количество | Конверсия с прошлого шага | Цена шага |", "|---|---|---|---|"]
    prev = None
    for k, title in STAGES:
        if k not in totals:
            continue
        n = totals[k]["count"]
        conv = f"{pct(n, prev):.1f}%" if prev else "—"
        price = f"{money(cost, n):,} ₽".replace(",", " ") if cost and n else "—"
        lines.append(f"| {title} | {n:,.0f} | {conv} | {price} |".replace(",", " "))
        prev = n
    if "regs" in totals and "sales" in totals:
        lines.append(f"\nРегистрация → оплата: **{pct(totals['sales']['count'], totals['regs']['count'])}%**")
    if "sales" in totals and cost:
        rev = totals["sales"]["amount"]
        lines.append(f"Выручка {rev:,.0f} ₽ · ROMI **{(rev - cost) / cost * 100:.0f}%** · ДРР {pct(cost, rev)}% · "
                     f"средний чек {money(rev, totals['sales']['count']) or 0:,} ₽".replace(",", " "))
        lines.append("ROMI и ДРР — по выручке, без маржи, налогов и комиссий платформ.")
    if cost and args.days:
        lines.append(f"\n**Бюджет в месяц ≈ {cost / args.days * 30:,.0f} ₽** (расход {cost:,.0f} ₽ за {args.days:.0f} дн.)".replace(",", " "))
    for k, title in STAGES:
        if k in totals and not totals[k]["has_utm"]:
            lines.append(f"\n⚠ {title}: в файле нет utm_campaign — по кампаниям этот шаг не разбит, только итог.")

    if len(agg):
        lines += ["", "## По кампаниям\n", md(table.head(30))]
    if unmatched_all:
        um = pd.DataFrame(unmatched_all).sort_values("количество", ascending=False)
        um.to_csv(out_dir / "unmatched_utm.tsv", sep="\t", index=False)
        lines += ["", "## Не сопоставлено с кампаниями (топ)\n", md(um.head(20))]
    text = "\n".join(lines) + "\n"
    (out_dir / "funnel_summary.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
