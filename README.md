# Insider Radar

Denný sken SEC Form 4 filingov. Beží ako GitHub Action, výsledok je statický dashboard.
Žiadny LLM, žiadny paušál, žiadna appka na PC.

## Sprevádzkovanie (10 minút)

1. **Vytvor repo** a nahraj tieto súbory. Repo daj **public** — Actions sú potom
   zadarmo bez limitu minút a GitHub Pages funguje. (Watchlist bude verejný;
   ak ti to vadí, drž repo private a maj 2000 minút/mesiac zadarmo.)

2. **Settings → Secrets and variables → Actions → New secret**
   - Názov: `SEC_USER_AGENT`
   - Hodnota: `Meno Priezvisko tvoj@email.sk`

   SEC to vyžaduje. Bez reálneho kontaktu ti vráti 403 a nič nestiahneš.

3. **Actions → Insider scan → Run workflow.** Prvý beh trvá pár minút.

4. **Dashboard:**
   - Hostovaný: Settings → Pages → Source: `main`, folder `/docs`.
   - Lokálne: `cd docs && python -m http.server` → http://localhost:8000
     (cez `file://` fetch nefunguje, prehliadač to zablokuje)

## Ladenie

| Čo | Kde |
|---|---|
| Bodovanie | `rules.py` — jediný súbor, ktorý budeš reálne meniť |
| Watchlist, okno | `.github/workflows/scan.yml`, sekcia `env` |
| Čas behu | `cron` v tom istom súbore |

Watchlist rapídne zrýchli beh: filtruje sa podľa CIK ešte pred sťahovaním filingov.

## Prečo iba kódy P a S

Form 4 pokrýva všetky pohyby insiderov, nielen obchody:

| Kód | Význam | Signál? |
|-----|--------|---------|
| **P** | nákup na otvorenom trhu | áno — insider dal vlastné peniaze |
| **S** | predaj na otvorenom trhu | slabý — dôvodov je desať |
| A | grant / odmena | nie, to je mzda |
| M | uplatnenie opcie | nie |
| F | akcie zrazené na daň | nie |
| G | dar | nie |

Skript berie iba P a S. Väčšina "insider trackerov" toto nerozlišuje, a preto
ukazuje šum.

## Test

    python test_parser.py

Overuje filter kódov, extrakciu, validáciu a bodovanie na syntetickom Form 4.
Nesiaha na sieť.
