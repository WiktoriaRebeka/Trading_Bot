import logging
import uuid
from flask import Flask, jsonify

# Importy z bibliotek współdzielonych
from shared_lib.config_loader import load_config
from shared_lib.firebase_client import initialize_firebase, get_symbols_to_watch_from_config

# Importy z bieżącego serwisu (bot_service)
from bot_service.bigquery_logger import initialize_bigquery
from bot_service.bot_logic import initialize_trading_services, process_new_alerts, run_trading_logic
from bot_service.fetch_from_firestore import fetch_new_alerts_since, load_last_processed_timestamp, save_last_processed_timestamp
from bot_service.bybit_executor import BybitExecutor

logger = logging.getLogger(__name__)

# Przenosimy funkcję konfiguracyjną tutaj, ale nie będziemy jej wywoływać na starcie
def configure_bybit_account(executor: BybitExecutor) -> bool:
    """
    Upewnia się, że wszystkie handlowane symbole są w trybie Isolated Margin.
    Zwraca True, jeśli wszystkie symbole są poprawnie skonfigurowane.
    """
    logger.info("--- ROZPOCZĘCIE KONFIGURACJI KONTRAKTÓW NA BYBIT ---")
    symbols_to_configure = get_symbols_to_watch_from_config()
    if not symbols_to_configure:
        logger.warning("Brak symboli do skonfigurowania w Firestore. Pomijam ten krok.")
        return True

    all_successful = True
    default_leverage = 10

    for symbol in symbols_to_configure:
        try:
            position_info = executor.get_position_info(symbol)
            
            if not position_info or float(position_info.get('size', '0')) == 0:
                # Brak aktywnej pozycji, możemy próbować konfigurować
                logger.info(f"[{symbol}] Brak aktywnej pozycji. Sprawdzanie/ustawianie trybu Isolated.")
                if not executor.set_isolated_margin(symbol, default_leverage):
                    # Sprawdzamy, czy błąd to nie "już ustawione"
                    position_after_fail = executor.get_position_info(symbol)
                    if position_after_fail and position_after_fail.get('tradeMode') == 1:
                        logger.info(f"[{symbol}] set_isolated_margin nie powiodło się, ale tryb jest już Isolated. Uznaję za sukces.")
                        continue
                    
                    logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD: Nie udało się ustawić trybu Isolated. Sprawdź uprawnienia klucza API i stan konta.")
                    all_successful = False
            else:
                # Pozycja istnieje, sprawdzamy tylko tryb
                if position_info.get('tradeMode') == 1:
                    logger.info(f"[{symbol}] jest już w trybie Isolated. OK.")
                else:
                    logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD: Wykryto aktywną pozycję w trybie Cross. Nie można automatycznie przełączyć. Wymagana ręczna interwencja!")
                    all_successful = False
        except Exception as e:
            logger.critical(f"[{symbol}] Nieoczekiwany błąd podczas konfiguracji: {e}", exc_info=True)
            all_successful = False
    
    if all_successful:
        logger.info("--- ZAKOŃCZONO SUKCESEM KONFIGURACJĘ KONTRAKTÓW NA BYBIT ---")
    else:
        logger.critical("--- KONFIGURACJA KONTRAKTÓW NA BYBIT ZAKOŃCZONA BŁĘDAMI ---")
        
    return all_successful

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
        
        # --- NOWA LOGIKA KONFIGURACJI "ON-DEMAND" ---
        if not app.config.get('BYBIT_CONFIG_COMPLETE', False):
            logger.info("Pierwsze uruchomienie cyklu. Uruchamiam konfigurację konta Bybit.")
            executor = app.config.get('BYBIT_EXECUTOR')
            if not executor:
                logger.critical("Brak instancji BybitExecutor w konfiguracji aplikacji!")
                return jsonify({"status": "error", "message": "Critical: BybitExecutor not found."}), 500

            config_ok = configure_bybit_account(executor)
            if config_ok:
                app.config['BYBIT_CONFIG_COMPLETE'] = True
                logger.info("Konfiguracja konta Bybit zakończona sukcesem.")
            else:
                logger.error("Konfiguracja konta Bybit nie powiodła się. Cykl zostanie przerwany. Sprawdź logi startowe.")
                return jsonify({"status": "error", "message": "Bybit account configuration failed. Manual intervention required."}), 503
        # --- KONIEC NOWEJ LOGIKI ---

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

def initialize_app_services(app: Flask):
    """Wykonuje szybką, nieblokującą inicjalizację w kontekście aplikacji."""
    with app.app_context():
        logger.info("Rozpoczynam szybką inicjalizację aplikacji `bot_service`.")
        
        load_config()
        firebase_ok = initialize_firebase()
        bigquery_ok = initialize_bigquery()
        trading_services_ok, executor = initialize_trading_services()

        # Przechowujemy instancję egzekutora w konfiguracji aplikacji do późniejszego użycia
        if executor:
            app.config['BYBIT_EXECUTOR'] = executor

        # Inicjalizacja jest teraz "udana", jeśli podstawowe klienty zostały utworzone.
        # Konfiguracja konta Bybit została przeniesiona do pierwszego cyklu.
        if firebase_ok and bigquery_ok and trading_services_ok:
            app.config['INITIALIZATION_SUCCESS'] = True
            logger.info("Podstawowe usługi zainicjalizowane. Aplikacja gotowa do startu.")
        else:
            app.config['INITIALIZATION_SUCCESS'] = False
            reasons = []
            if not firebase_ok: reasons.append("Firebase failed")
            if not bigquery_ok: reasons.append("BigQuery failed")
            if not trading_services_ok: reasons.append("BybitExecutor failed")
            final_reason = ", ".join(reasons)
            app.config['INITIALIZATION_FAILURE_REASON'] = final_reason
            logger.critical(f"Krytyczny błąd podczas inicjalizacji podstawowych usług. Powód: {final_reason}")