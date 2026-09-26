# Backtest MSI Etap A — Cloud Shell

Paczka **nie zmienia** działającego bota. Nie deployuje, nie restartuje Cloud Run, nie zapisuje do BigQuery ani Firestore. Jedyne wyjście to pliki lokalne w `data/` po rozpakowaniu.

Projekt GCP: `trading-bot-463318`

---

## 1. Wgraj i rozpakuj

W Cloud Shell (ikonka terminala w konsoli Google Cloud), w katalogu domowym:

```bash
mkdir -p ~/backtest && cd ~/backtest
# wgraj backtest_package.zip (Upload w menu Cloud Shell) albo:
# gsutil cp gs://<twój-bucket>/backtest_package.zip .
unzip -o backtest_package.zip
cd backtest_package
```

Struktura po rozpakowaniu:

```text
backtest_package/
  URUCHOM.md
  requirements_backtest.txt
  orderflow_engine/msi_engine.py
  orderflow_engine/config_symbols.py
  backtest/msi_stage_a.py
```

---

## 2. Projekt i poświadczenia

```bash
gcloud config set project trading-bot-463318
gcloud config get-value project
```

Ma wypisać `trading-bot-463318`.

Application Default Credentials (BigQuery **tylko odczyt** SELECT):

```bash
gcloud auth application-default login
```

W Cloud Shell często ADC już jest. Jeśli query pada na credentials, zobacz sekcję na dole.

---

## 3. venv i biblioteki

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements_backtest.txt
```

---

## 4. Uruchomienie Etapu A

Z katalogu `backtest_package` (venv aktywny):

```bash
export PYTHONPATH="$PWD"
python3 backtest/msi_stage_a.py \
  --start 2026-07-15T00:00:00Z \
  --compare-start 2026-08-15T15:59:00Z \
  --workers 4
```

To robi kolejno:

1. Pobiera publiczne kline M1 Bybit (bez kluczy API) dla 66 symboli z `config_symbols.py`, od `2026-07-15 00:00 UTC` do ostatniej **zamkniętej** minuty.
2. Zapisuje `data/bybit_m1_parquet/symbol=XXX.parquet` oraz `data/reports/m1_data_report.csv`.
3. Replay `MsiEngine.on_candle_close` od 15.07 (rozgrzewka); do porównania bierze `OB_NEW` od `2026-08-15 15:59 UTC`.
4. Czyta BigQuery **wyłącznie SELECT** z `trading-bot-463318.trading_analytics.orderblock_events`.
5. Raporty w `data/reports/`:
   - `ob_new_comparison.json`
   - `ob_new_comparison_per_symbol.csv`
   - `ob_new_only_live.csv`
   - `ob_new_only_backtest.csv`

Szacowany czas KROKU 1: kilkadziesiąt minut (ok. 70+ dni × 66 par, paginacja 1000, ~3 req/s).

### Opcje pomocnicze

Tylko dane (bez BigQuery):

```bash
python3 backtest/msi_stage_a.py --data-only --workers 4
```

Tylko porównanie (parquet już jest):

```bash
python3 backtest/msi_stage_a.py --compare-only \
  --compare-start 2026-08-15T15:59:00Z
```

Wymuś ponowne pobranie świec:

```bash
python3 backtest/msi_stage_a.py --refresh-data --workers 4
```

Jeśli job BigQuery wraca `Not found: Dataset` / zła lokalizacja:

```bash
python3 backtest/msi_stage_a.py --compare-only --bq-location US
# albo
python3 backtest/msi_stage_a.py --compare-only --bq-location EU
```

Domyślnie `--bq-location EU` (region zbliżony do `europe-central2`).

---

## 5. Błędy poświadczeń BigQuery

Objawy: `DefaultCredentialsError`, `Reauthentication is needed`, `403 Access Denied`, `The project is not set`.

```bash
gcloud config set project trading-bot-463318
gcloud auth login
gcloud auth application-default login
gcloud auth application-default set-quota-project trading-bot-463318
```

Konto musi mieć prawo **odczytu** tabeli (`roles/bigquery.dataViewer` + `roles/bigquery.jobUser` wystarczy). Harness **nie** woła INSERT/UPDATE/LOAD/DELETE.

Sprawdzenie SELECT ręcznie:

```bash
bq query --use_legacy_sql=false --location=EU \
  'SELECT COUNT(*) AS n
   FROM `trading-bot-463318.trading_analytics.orderblock_events`
   WHERE event_type = "OB_NEW"
     AND event_ts >= TIMESTAMP("2026-08-15 15:59:00+00")'
```

---

## 6. Błędy Bybit (429 / retCode 10006)

Skrypt czeka i ponawia. Jeśli limity nadal padają, zmniejsz równoległość:

```bash
python3 backtest/msi_stage_a.py --workers 2 --max-rps 1.5
```

Brak kluczy API — endpoint publiczny `GET https://api.bybit.com/v5/market/kline`.

---

## 7. Czego ta paczka NIE robi

- nie deployuje i nie restartuje `orderflow-engine` / `bot-service`
- nie zapisuje do BigQuery, Firestore, Bybit
- nie składa zleceń, nie liczy PnL (to kolejne etapy)
