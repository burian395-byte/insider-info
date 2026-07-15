"""
Insider Radar — zbiera SEC Form 4 filingy, filtruje reálne otvorené nákupy,
boduje ich podľa pravidiel v rules.py a zapíše data/latest.json.

Spúšťa sa cez GitHub Actions. Bez LLM — čisto deterministické.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import logging
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

from rules import score_signal, RULES

# --- Konfigurácia -----------------------------------------------------------

# SEC vyžaduje kontakt v User-Agent, inak vráti 403. Nastav si vlastný.
USER_AGENT = os.environ.get("SEC_USER_AGENT", "Insider Radar michal@example.com")

LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "7"))
# Prázdny watchlist = široký sken. S watchlistom je beh rádovo rýchlejší.
WATCHLIST = [t.strip().upper() for t in os.environ.get("WATCHLIST", "").split(",") if t.strip()]
MAX_FILINGS = int(os.environ.get("MAX_FILINGS", "600"))

SEC = "https://www.sec.gov"
RATE_LIMIT_SLEEP = 0.11  # SEC povoľuje 10 req/s

OUT = Path(__file__).parent / "data" / "latest.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("radar")

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})


def get(url: str, retries: int = 3) -> requests.Response | None:
    """GET s rate-limitom a retry. Vracia None namiesto vyhadzovania výnimky."""
    for attempt in range(retries):
        try:
            time.sleep(RATE_LIMIT_SLEEP)
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
            log.warning("HTTP %s pre %s (pokus %s)", r.status_code, url, attempt + 1)
            time.sleep(2 ** attempt)
        except requests.RequestException as e:
            log.warning("Chyba siete: %s (pokus %s)", e, attempt + 1)
            time.sleep(2 ** attempt)
    return None


# --- Dátový model -----------------------------------------------------------

@dataclass
class Signal:
    ticker: str
    company: str
    actor: str
    role: str
    action: str            # "buy" | "sell"
    code: str              # SEC transaction code
    shares: float | None
    price: float | None
    amountUsd: float | None
    amountLabel: str
    txDate: str
    filedDate: str
    lagDays: int | None
    clusterCount: int
    sourceUrl: str


# --- EDGAR ------------------------------------------------------------------

def ticker_map() -> dict[str, str]:
    """CIK -> ticker. SEC publikuje kompletný zoznam ako jeden JSON."""
    r = get(f"{SEC}/files/company_tickers.json")
    if not r:
        log.error("Nepodarilo sa stiahnuť company_tickers.json")
        return {}
    out = {}
    for row in r.json().values():
        out[str(row["cik_str"]).zfill(10)] = row["ticker"].upper()
    log.info("Načítaných %s CIK->ticker mapovaní", len(out))
    return out


def daily_form4_index(day: date) -> list[tuple[str, str, str]]:
    """Vracia (cik, company, filename) pre všetky Form 4 podané v daný deň."""
    qtr = (day.month - 1) // 3 + 1
    url = f"{SEC}/Archives/edgar/daily-index/{day.year}/QTR{qtr}/master.{day:%Y%m%d}.idx"
    r = get(url)
    if not r:
        return []  # víkend, sviatok, alebo ešte nie je zverejnené

    rows = []
    for line in r.text.splitlines():
        parts = line.split("|")
        if len(parts) != 5:
            continue
        cik, company, form_type, _filed, filename = parts
        if form_type.strip() != "4":
            continue
        rows.append((cik.strip().zfill(10), company.strip(), filename.strip()))
    return rows


OWNERSHIP_RE = re.compile(r"<ownershipDocument>.*?</ownershipDocument>", re.DOTALL)


def text_of(node, path: str) -> str | None:
    el = node.find(path)
    return el.text.strip() if el is not None and el.text else None


def num_of(node, path: str) -> float | None:
    v = text_of(node, path)
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def parse_form4(raw: str, url: str, filed: str) -> list[Signal]:
    """Vytiahne ownershipDocument z full-text submission a rozparsuje transakcie."""
    m = OWNERSHIP_RE.search(raw)
    if not m:
        return []
    try:
        doc = ET.fromstring(m.group(0))
    except ET.ParseError:
        return []

    ticker = (text_of(doc, "issuer/issuerTradingSymbol") or "").upper()
    company = text_of(doc, "issuer/issuerName") or ""

    owner = doc.find("reportingOwner")
    actor = text_of(owner, "reportingOwnerId/rptOwnerName") if owner is not None else None
    role = ""
    if owner is not None:
        rel = owner.find("reportingOwnerRelationship")
        if rel is not None:
            bits = []
            if text_of(rel, "officerTitle"):
                bits.append(text_of(rel, "officerTitle"))
            if text_of(rel, "isDirector") in ("1", "true"):
                bits.append("Director")
            if text_of(rel, "isTenPercentOwner") in ("1", "true"):
                bits.append("10% owner")
            role = " / ".join(bits)

    out: list[Signal] = []
    for tx in doc.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = text_of(tx, "transactionCoding/transactionCode") or ""

        # KĽÚČOVÝ FILTER. Iba P a S sú reálne obchody na otvorenom trhu.
        # A = grant/odmena, M = uplatnenie opcie, F = zrážka na daň, G = dar.
        # Väčšina "insider trackerov" toto nerozlišuje a preto ukazuje šum.
        if code not in ("P", "S"):
            continue

        shares = num_of(tx, "transactionAmounts/transactionShares/value")
        price = num_of(tx, "transactionAmounts/transactionPricePerShare/value")
        tx_date = text_of(tx, "transactionDate/value") or ""
        amount = shares * price if (shares and price) else None

        lag = None
        if tx_date and filed:
            try:
                lag = (datetime.strptime(filed, "%Y-%m-%d") - datetime.strptime(tx_date, "%Y-%m-%d")).days
            except ValueError:
                pass

        out.append(Signal(
            ticker=ticker,
            company=company,
            actor=actor or "neznámy",
            role=role or "—",
            action="buy" if code == "P" else "sell",
            code=code,
            shares=shares,
            price=price,
            amountUsd=amount,
            amountLabel=f"${amount:,.0f}" if amount else "—",
            txDate=tx_date,
            filedDate=filed,
            lagDays=lag,
            clusterCount=1,
            sourceUrl=url,
        ))
    return out


# --- Validácia --------------------------------------------------------------

def is_valid(s: Signal, today: date) -> tuple[bool, str]:
    """Deterministická kontrola. Toto je to, čo by druhý LLM nespravil lepšie."""
    if not s.ticker:
        return False, "chýba ticker (nekótovaný emitent)"
    if not s.txDate:
        return False, "chýba dátum transakcie"
    try:
        tx = datetime.strptime(s.txDate, "%Y-%m-%d").date()
    except ValueError:
        return False, "nevalidný dátum"
    if tx > today:
        return False, "dátum v budúcnosti"
    if s.lagDays is not None and s.lagDays < 0:
        return False, "podané pred transakciou"
    if s.lagDays is not None and s.lagDays > 365:
        return False, "transakcia staršia než rok"
    if not s.shares or s.shares <= 0:
        return False, "nulový objem"
    if s.price is not None and s.price <= 0:
        return False, "nulová cena"
    return True, ""


def add_clusters(signals: list[Signal]) -> None:
    """Koľko RÔZNYCH insiderov nakupovalo v tej istej firme v okne."""
    buyers: dict[str, set[str]] = {}
    for s in signals:
        if s.action == "buy":
            buyers.setdefault(s.ticker, set()).add(s.actor)
    for s in signals:
        s.clusterCount = len(buyers.get(s.ticker, set())) if s.action == "buy" else 1


# --- Hlavný beh -------------------------------------------------------------

def main() -> int:
    today = date.today()
    log.info("Okno: %s dní | Watchlist: %s", LOOKBACK_DAYS, WATCHLIST or "(široký sken)")

    cik2ticker = ticker_map()
    wanted_ciks = None
    if WATCHLIST:
        wanted_ciks = {c for c, t in cik2ticker.items() if t in WATCHLIST}
        missing = set(WATCHLIST) - {cik2ticker[c] for c in wanted_ciks}
        if missing:
            log.warning("Tieto tickery SEC nepozná (nie sú US-kótované?): %s", ", ".join(sorted(missing)))
        if not wanted_ciks:
            log.error("Žiadny ticker z watchlistu sa nenašiel. Končím.")
            return 1

    # 1. FETCH
    filings: list[tuple[str, str, str]] = []
    for i in range(LOOKBACK_DAYS):
        day = today - timedelta(days=i)
        if day.weekday() >= 5:
            continue
        rows = daily_form4_index(day)
        if wanted_ciks:
            rows = [r for r in rows if r[0] in wanted_ciks]
        log.info("%s: %s Form 4", day, len(rows))
        filings.extend((cik, comp, fn) for cik, comp, fn in rows)

    if len(filings) > MAX_FILINGS:
        log.warning("Orezávam %s -> %s filingov (MAX_FILINGS)", len(filings), MAX_FILINGS)
        filings = filings[:MAX_FILINGS]

    log.info("Sťahujem %s filingov…", len(filings))
    signals: list[Signal] = []
    for n, (cik, _company, filename) in enumerate(filings, 1):
        if n % 50 == 0:
            log.info("  %s/%s", n, len(filings))
        url = f"{SEC}/Archives/{filename}"
        r = get(url)
        if not r:
            continue
        # dátum podania je v názve accession-u nespoľahlivo — berieme z indexu dňa
        filed_match = re.search(r"FILED AS OF DATE:\s*(\d{8})", r.text)
        filed = (
            datetime.strptime(filed_match.group(1), "%Y%m%d").strftime("%Y-%m-%d")
            if filed_match else ""
        )
        signals.extend(parse_form4(r.text, url, filed))

    log.info("Rozparsovaných transakcií (kód P/S): %s", len(signals))

    # 2. VALIDATE
    valid, rejected = [], {}
    for s in signals:
        ok, why = is_valid(s, today)
        if ok:
            valid.append(s)
        else:
            rejected[why] = rejected.get(why, 0) + 1
    if rejected:
        log.info("Zamietnuté: %s", rejected)

    # 3. SCORE
    add_clusters(valid)
    rows = []
    for s in valid:
        d = asdict(s)
        total, hits = score_signal(d)
        d["score"] = total
        d["scoreHits"] = hits
        rows.append(d)
    rows.sort(key=lambda d: (-d["score"], d["filedDate"]))

    payload = {
        "generatedAt": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "lookbackDays": LOOKBACK_DAYS,
        "watchlist": WATCHLIST,
        "counts": {
            "filingsFetched": len(filings),
            "transactions": len(signals),
            "valid": len(valid),
            "rejected": rejected,
        },
        "rules": [{"id": r.id, "label": r.label, "why": r.why, "points": r.points} for r in RULES],
        "signals": rows,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    log.info("Zapísané %s -> %s signálov", OUT, len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
