# Lokalizacja: bot_service/app_setup.py
import logging
import uuid
from flask import Flask, jsonify

# Importy z bibliotek współdzielonych
from shared_lib.config_loader import load_config
from shared_lib.firebase_client import initialize_firebase

# Importy z bieżącego serwisu (bot_service)
from bot_service.bigquery_logger import initialize_bigquery  # <-- KLUCZOWA POPRAWKA
from bot_service.bot_logic import initialize_trading_services, process_new_alerts, run_trading_logic
from bot_service.fetch_from_firestore import fetch_new_alerts_since, load_last_processed_timestamp, save_last_processed_timestamp

from bot_service.bybit_executor import BybitExecutor, BybitAPIError
from shared_lib.firebase_client import get_symbols_to_watch_from_config

logger = logging.getLogger(__name__)

def register_endpoints(app: Flask):
    """Rejestruje wszystkie endpointy aplikacji."""
    @app.route('/')
    def health_check():
        return "Trading Bot Service is running.", 200

    @app.route('/health')
    def deep_health_check():
        if app.config.get('INITIALIZATION_SUCCESS', False):
            return jsonify({"status": "healthy"}), 200
        else:
            reason = app.config.get('INITIALIZATION_FAILURE_REASON', 'Unknown initialization error.')
            return jsonify({"status": "unhealthy", "reason": reason}), 503

    @app.route('/run-bot-cycle', methods=['POST'])
    def run_bot_cycle_endpoint():
        cycle_id = str(uuid.uuid4())
        logger.info("--- ROZPOCZĘCIE CYKLU BOTA ---", extra={"json_fields": {"cycle_id": cycle_id}})

        if not app.config.get('INITIALIZATION_SUCCESS', False):
             reason = app.config.get('INITIALIZATION_FAILURE_REASON', 'Unknown initialization error.')
             logger.error(f"Zatrzymano cykl, ponieważ aplikacja nie została poprawnie zainicjalizowana. Powód: {reason}", extra={"json_fields": {"cycle_id": cycle_id}})
             return jsonify({"status": "error", "message": f"Service is unhealthy: {reason}"}), 503
        try:
            last_ts = load_last_processed_timestamp()
            new_alerts, new_ts = fetch_new_alerts_since(last_ts)
            if new_alerts:
                logger.info(f"Przetwarzam {len(new_alerts)} nowych alertów.", extra={"json_fields": {"cycle_id": cycle_id}})
                process_new_alerts(new_alerts)
                if new_ts and new_ts > last_ts:
                    save_last_processed_timestamp(new_ts)
            
            run_trading_logic()

            logger.info("--- ZAKOŃCZENIE CYKLU BOTA ---", extra={"json_fields": {"cycle_id": cycle_id, "status": "success"}})
            return jsonify({"status": "success", "cycle_id": cycle_id}), 200
        except Exception as e:
            logger.error(f"Krytyczny błąd w głównym cyklu bota: {e}", exc_info=True, extra={"json_fields": {"cycle_id": cycle_id, "status": "error"}})
            return jsonify({"status": "error", "message": str(e), "cycle_id": cycle_id}), 500

def configure_bybit_account(executor: BybitExecutor) -> bool:
    """
    Upewnia się, że wszystkie handlowane symbole są w trybie Isolated Margin.
    Wywoływana raz podczas startu aplikacji.
    Zwraca True, jeśli wszystkie symbole są poprawnie skonfigurowane.
    """
    logger.info("--- ROZPOCZĘCIE KONFIGURACJI KONTRAKTÓW NA BYBIT ---")
    symbols_to_configure = get_symbols_to_watch_from_config()
    if not symbols_to_configure:
        logger.warning("Brak symboli do skonfigurowania w Firestore. Pomijam ten krok.")
        return True # Traktujemy to jako sukces

    all_successful = True
    default_leverage = 10 # Bezpieczna, domyślna dźwignia

    for symbol in symbols_to_configure:
        try:
            position_info = executor.get_position_info(symbol)
            
            # Jeśli nie ma informacji o pozycji, zakładamy, że można ją skonfigurować
            if not position_info:
                logger.info(f"[{symbol}] Brak informacji o pozycji. Próba ustawienia trybu Isolated i dźwigni {default_leverage}x.")
                if not executor.set_isolated_margin(symbol, default_leverage):
                    logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD: Nie udało się ustawić trybu Isolated. Sprawdź uprawnienia klucza API i stan konta.")
                    all_successful = False
                continue

            # Jeśli jest informacja, sprawdzamy tryb
            trade_mode = position_info.get('tradeMode')
            current_leverage = int(float(position_info.get('leverage', '0')))

            if trade_mode == 1: # 1 to Isolated
                logger.info(f"[{symbol}] jest już w trybie Isolated. Dźwignia: {current_leverage}x. OK.")
                # Opcjonalnie: można też upewnić się, że dźwignia jest poprawna
                if current_leverage != default_leverage:
                     logger.info(f"[{symbol}] Dźwignia inna niż domyślna. Próba ustawienia na {default_leverage}x.")
                     executor.set_isolated_margin(symbol, default_leverage) # próbujemy ustawić, ale nie traktujemy błędu jako krytycznego
            else: # 0 to Cross
                logger.warning(f"[{symbol}] jest w trybie Cross Margin! Próba przełączenia na Isolated.")
                if not executor.set_isolated_margin(symbol, default_leverage):
                    logger.critical(
                        f"[{symbol}] KRYTYCZNY BŁĄD: Nie można przełączyć z Cross na Isolated. "
                        f"Zaloguj się na konto Bybit i zrób to ręcznie! Może istnieć otwarta pozycja/zlecenie."
                    )
                    all_successful = False

        except Exception as e:
            logger.critical(f"[{symbol}] Nieoczekiwany błąd podczas konfiguracji: {e}", exc_info=True)
            all_successful = False
    
    if all_successful:
        logger.info("--- ZAKOŃCZONO SUKCESEM KONFIGURACJĘ KONTRAKTÓW NA BYBIT ---")
    else:
        logger.critical("--- KONFIGURACJA KONTRAKTÓW NA BYBIT ZAKOŃCZONA BŁĘDAMI ---")
        
    return all_successful

def initialize_app_services(app: Flask):
    """Wykonuje całą logikę inicjalizacji w kontekście aplikacji."""
    with app.app_context():
        logger.info("Rozpoczynam konfigurację aplikacji `bot_service` wewnątrz kontekstu.")
        
        # Krok 1: Załaduj konfigurację i sekrety.
        load_config()

        # Krok 2: Inicjalizuj podstawowe usługi.
        firebase_ok = initialize_firebase()
        bigquery_ok = initialize_bigquery()
        
        # Krok 3: Inicjalizuj usługi tradingowe.
        trading_services_ok, executor = initialize_trading_services() # Zmieniamy, by zwracało też instancję

        # Krok 4: Uruchom konfigurację konta Bybit (NOWY KROK)
        bybit_config_ok = False
        if trading_services_ok and executor:
            bybit_config_ok = configure_bybit_account(executor)
        else:
            logger.critical("Pominięto konfigurację konta Bybit, ponieważ BybitExecutor nie został zainicjalizowany.")

        # Krok 5: Sprawdź, czy WSZYSTKIE kluczowe usługi zostały zainicjalizowane poprawnie.
        # Dodajemy `bybit_config_ok` do warunku
        if firebase_ok and bigquery_ok and trading_services_ok and bybit_config_ok:
            app.config['INITIALIZATION_SUCCESS'] = True
            logger.info("Aplikacja Flask [bot_service] została pomyślnie utworzona i skonfigurowana.")
        else:
            app.config['INITIALIZATION_SUCCESS'] = False
            # Logika zbierania powodów błędu (można ją rozbudować o powód z bybit_config_ok)
            reasons = []
            if not firebase_ok: reasons.append("Firebase failed")
            if not bigquery_ok: reasons.append("BigQuery failed")
            if not trading_services_ok: reasons.append("BybitExecutor failed")
            if not bybit_config_ok: reasons.append("Bybit account configuration failed")
            final_reason = ", ".join(reasons)
            app.config['INITIALIZATION_FAILURE_REASON'] = final_reason
            logger.critical(f"Krytyczny błąd podczas inicjalizacji. Aplikacja będzie zwracać błędy 503. Powód: {final_reason}")



