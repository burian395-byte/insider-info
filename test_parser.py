"""Overuje parsovanie, filter transakčných kódov, validáciu a bodovanie."""

import sys
from datetime import date

sys.path.insert(0, ".")
from scan import parse_form4, is_valid, add_clusters, Signal
from rules import score_signal

FIXTURE = """
-----BEGIN PRIVACY-ENHANCED MESSAGE-----
FILED AS OF DATE:		20260713
<DOCUMENT>
<TYPE>4
<XML>
<?xml version="1.0"?>
<ownershipDocument>
  <periodOfReport>2026-07-11</periodOfReport>
  <issuer>
    <issuerCik>0000320193</issuerCik>
    <issuerName>Testco Industries Inc.</issuerName>
    <issuerTradingSymbol>TSTC</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Novak Jana</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector>
      <isOfficer>1</isOfficer>
      <officerTitle>Chief Financial Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-07-11</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>40000</value></transactionShares>
        <transactionPricePerShare><value>55.00</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-07-11</value></transactionDate>
      <transactionCoding><transactionCode>A</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>99999</value></transactionShares>
        <transactionPricePerShare><value>0</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-07-11</value></transactionDate>
      <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>50000</value></transactionShares>
        <transactionPricePerShare><value>12.00</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
</XML>
</DOCUMENT>
"""

sig = parse_form4(FIXTURE, "https://example/f4.txt", "2026-07-13")

print("=== FILTER KÓDOV ===")
print(f"transakcií v XML: 3 (P, A, M)")
print(f"prešlo filtrom:   {len(sig)}  -> {[s.code for s in sig]}")
assert len(sig) == 1 and sig[0].code == "P", "filter kódov zlyhal"
print("OK: grant (A) a uplatnenie opcie (M) správne zahodené\n")

s = sig[0]
print("=== EXTRAKCIA ===")
for k in ("ticker", "company", "actor", "role", "action", "shares", "price", "amountLabel", "txDate", "filedDate", "lagDays"):
    print(f"  {k:12} = {getattr(s, k)}")
assert s.ticker == "TSTC" and s.amountUsd == 2_200_000 and s.lagDays == 2
print("OK\n")

print("=== VALIDÁCIA ===")
ok, why = is_valid(s, date(2026, 7, 15))
print(f"  platný: {ok} {why}")
bad = Signal(**{**s.__dict__, "txDate": "2027-01-01", "lagDays": -200})
print(f"  budúci dátum odmietnutý: {not is_valid(bad, date(2026,7,15))[0]}  ({is_valid(bad, date(2026,7,15))[1]})")
assert ok and not is_valid(bad, date(2026, 7, 15))[0]
print("OK\n")

print("=== KLASTER + SKÓRE ===")
peers = [
    s,
    Signal(**{**s.__dict__, "actor": "Kovac Peter"}),
    Signal(**{**s.__dict__, "actor": "Horvath Eva"}),
]
add_clusters(peers)
print(f"  clusterCount: {peers[0].clusterCount}")
total, hits = score_signal(peers[0].__dict__)
for h in hits:
    print(f"   {h['points']:+d}  {h['label']}")
print(f"  SPOLU: {total}")
assert peers[0].clusterCount == 3 and total == 8
print("OK — všetky testy prešli")
