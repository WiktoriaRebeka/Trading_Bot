# Specyfikacja strategii MSI OrderBlock

Dokument wiążący: zasady decyzji handlowych + zgodność z aktualnym kodem  
(`orderflow_engine/msi_engine.py`, `bot_service/msi_order_handler.py`, `shared_lib/ob_execution.py`).  
Ostatnia aktualizacja: 2026-10-12.

---

## 1. Cel

Handel strefami **Order Block** wynikającymi ze struktury rynku MSI (Market Structure)  
na świecach **1M** Bybit linear (USDT). Wejście **Limit GTC** z pełnym **SL + TP (2R + fee)**.

---

## 2. Definicja strefy OB

- OB = **cała świeca oznaczająca** (korpus + knoty): `ob_high = candle.high`, `ob_low = candle.low`.
- Przebicia strukturalne (BOS, CHOCH, liquidity grab) liczą się **knotami**: `HIGH` / `LOW`, nie close.
- **LONG** (Higher LOW): `entry = HIGH(OB)`, `SL = LOW(OB)`.
- **SHORT** (Lower HIGH): `entry = LOW(OB)`, `SL = HIGH(OB)`.
- `1R = risk_ob = |entry − SL|` (= wysokość strefy OB).

---

## 3. Target (TP) z prowizją — oba kierunki

Stała: `ROUND_TRIP_FEE_PCT = 0.00075` (0,075%).

| Kierunek | Formuła |
|----------|---------|
| LONG | `TP = entry + 2R + 0.00075 × entry` |
| SHORT | `TP = entry − 2R − 0.00075 × entry` |

Implementacja: `shared_lib/ob_execution.py` → `compute_ob_tp`.

Zaokrąglenie do ticka w `msi_order_handler` (`_prepare_msi_order`):

| Poziom | LONG | SHORT | Sens |
|--------|------|-------|------|
| entry | `down` | `up` | Limit nie gorszy względem strefy |
| SL | `up` | `down` | SL nie wewnątrz strefy w złą stronę |
| TP | `up` | `down` | TP **dalej** od entry (bezpieczna strona R) |

SHORT: fee jest odejmowane od entry (nie pomijane). TP po `down` nie przybliża się do entry.

---

## 4. Emisja sygnału i egzekucja

1. `MsiEngine.on_candle_close` (oficjalne kline 1M) → przy `OB_NEW` alert do `bot_service`.
2. **Karencja wyłączona** (`MSI_COOLDOWN_ENABLED=false` domyślnie) i **tak zostaje**.  
   Limit GTC składany od razu po `OB_NEW` (bez 30 min / failed-break).
3. **Wejście od razu jest setupem** — także gdy w chwili składania cena jest już na `entry`  
   lub w strefie OB (Limit często marketable / taker).  
   **Pomijamy wyłącznie**, gdy **lastPrice jest już za SL** → `SKIPPED_SL_ALREADY_HIT`.
4. Flaga `placed_marketable` (+ bid/ask/last przy place) → **tylko analiza w BQ**,  
   **nie** służy do pomijania sygnałów.

---

## 5. Struktura MSI (silnik) — skrót operacyjny

- Persystencja stanu: Firestore `msi_state` (resume po restarcie).
- Fazy: START → INITIAL_STRUCTURE → PRE_FIRST_OB → HL_LH_CYCLE  
  (`CHOCH_PENDING` tylko legacy — przy wczytaniu migracja do `HL_LH_CYCLE`).
- **Liquidity** po oznaczeniu OB **zostaje na swoim poziomie** i czeka na powrót ceny  
  (grab / cykl) — to zamierzone zachowanie, nie błąd.

### Zmiana trendu (zastępuje CHOCH_PENDING)

Przebicia liczone **knotami** (`HIGH` / `LOW`). Silnik śledzi `ob_range_extreme_candle`
od powstania aktualnego OB (LONG → świeca z najwyższym HIGH; SHORT → z najniższym LOW).

| Stan | Warunek | Skutek |
|------|---------|--------|
| Trend UP (OB LONG) | `LOW` świecy **&lt;** `OB.low` | Natychmiast trend = DOWN. Nowy OB **SHORT** = świeca z **najwyższym HIGH** od powstania tego LONG (entry=`LOW`, SL=`HIGH`). Event `CHOCH` mode=`TREND_CHANGE` + `OB_NEW`. |
| Trend DOWN (OB SHORT) | `HIGH` świecy **&gt;** `OB.high` | Natychmiast trend = UP. Nowy OB **LONG** = świeca z **najniższym LOW** od powstania tego SHORT (entry=`HIGH`, SL=`LOW`). Event `CHOCH` mode=`TREND_CHANGE` + `OB_NEW`. |

Po zmianie trendu świeca nowego OB = **nowa Initial Candle**. Silnik buduje strukturę
w nowym kierunku od początku (sekcja III/IV: Temporary HIGH/LOW + Temporary Liquidity
→ LIQUIDITY + BOS). Przy BOS **nie** emituje drugiego `OB_NEW` na tej samej świecy IC
(OB już powstał przy zmianie trendu) — wchodzi w cykl V (`WAIT_LIQ_GRAB` z Liquidity
z budowy IV, nie z HIGH/LOW OB). Potem grab → BOS → **kolejne OB w trendzie**.

Zmiana trendu ma **pierwszeństwo** przed III/IV/V na tej samej świecy.

**Bez zmian:** kolejne OB w trendzie (grab Liquidity → BOS), zamrożona Liquidity w
`WAIT_LIQ_GRAB`, filtr wysokości 0.2% po stronie bota.

**Migracja:** snapshot z `phase=CHOCH_PENDING` → przy pierwszej świecy (lub `import_state`
+ `on_candle`) przejście do `HL_LH_CYCLE`; ekstremum = `current_ob.candle` jeśli brak
`ob_range_extreme_candle`. Kolejne przebicie `OB.high`/`OB.low` od razu zmienia trend.

Szczegóły BOS / HL / LH / pierwszego OB: kod `msi_engine.py` + PDF MSI  
(ten dokument nie zastępuje PDF, tylko wiąże decyzje handlowe z kodem).

---

## 6. Reguła jednej pozycji na symbol

| Sytuacja | Zachowanie |
|----------|------------|
| Brak pozycji na symbolu | Place Limit GTC + SL + TP |
| Jest pozycja (Bybit) | Nowy OB → `DEFERRED_POSITION_OPEN` (najnowszy); starsze deferred → `CANCELLED_SUPERSEDED` |
| Po zamknięciu pozycji | Place najnowszego deferred **albo** `SKIPPED_SL_ALREADY_HIT` jeśli last już za SL |
| Oczekujące limity MSI | Przy nowym OB: supersede / cancel zgodnie z handlerem (pending statuses) |

Źródło prawdy pozycji: Bybit; fallback Firestore `OPEN` przy błędzie API.

---

## 7. Statusy `active_orders` (MSI) — wybrane

| Status | Znaczenie |
|--------|-----------|
| `PLACING` / `PLACED` | Limit wysłany / potwierdzony |
| `OPEN` | Fill + pozycja |
| `DEFERRED_POSITION_OPEN` | OB odłożony (zajęta pozycja) |
| `SKIPPED_SL_ALREADY_HIT` | Nie składamy — cena już za SL |
| `CLOSED_STALE` | OPEN bez pozycji Bybit (API OK, size=0), OPEN ≥ 10 min od `position_opened_at` |
| `CANCELLED_SUPERSEDED` | Zastąpiony nowszym OB / deferred |

Match zamknięć PnL: `orderLinkId` → `slOrderId` / `tpOrderId` (także przy statusie
`PLACED`/`PLACING`) → most Bybit (OPEN+PLACED+PLACING) → fallback dokładnie 1× kandydat.
`slOrderId`/`tpOrderId` zapisywane od razu po place (nie dopiero przy OPEN).

---

## 8. Dane do analizy (nie do filtrowania entry)

- `orderblock_events`: OB_NEW, ORDER_PLACED (`raw_context`: `placed_marketable`, ceny przy place), outcome.
- `real_trades_history`: PnL z Bybit; pola OB (`planned_*`, `realized_r`) po udanym matchu.
- Analiza podstawowa: win rate / USDT / czas w pozycji (w tym wejścia marketable).
- Analiza głębsza: typ OB, CHOCH, wysokość strefy, sesja — **po** ocenie podstawowej.

---

## 9. Świadomie NIE robimy

- Nie włączamy karencji 30 min / failed-break jako reguły domyślnej.
- Nie pomijamy sygnału tylko dlatego, że Limit jest marketable / cena w strefie.
- Nie traktujemy „liquidity czeka na powrót” jako błędu silnika.
- Nie restartujemy bota commitem/pushem bez potrzeby — zmiany docs/kodu partiami.

---

## 10. Mapa plików

| Obszar | Plik |
|--------|------|
| Silnik struktury | `orderflow_engine/msi_engine.py` |
| Alert OB → bot | `orderflow_engine/msi_trade_signal.py` |
| Place / defer / skip SL | `bot_service/msi_order_handler.py` |
| Entry/SL/TP/sanity | `shared_lib/ob_execution.py` |
| Karencja (OFF) | `bot_service/msi_cooldown_logic.py` |
| Updater / PnL / stale | `bot_service/bot_logic.py` |
