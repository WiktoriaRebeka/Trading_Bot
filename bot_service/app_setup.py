# Lokalizacja: bot_service/app_setup.py

import logging
import uuid
from flask import Flask, jsonify
from typing import Optional, Tuple

from shared_lib.config_loader import load_config
from shared_lib.firebase_client import initialize_firebase, get_symbols_to_watch_from_config
from shared_lib.config import config 

from bot_service.bigquery_logger import initialize_bigquery

import bot_service.bot_logic as bot_logic_module
from bot_service.fetch_from_firestore import fetch_new_alerts_since, load_last_processed_timestamp, save_last_processed_timestamp
from bot_service.bybit_executor import BybitExecutor

logger = logging.getLogger(__name__)

def initialize_trading_services() -> Tuple[bool, Optional[BybitExecutor]]:
    """
    Inicjalizuje BybitExecutor.
    Zwraca krotkę (status_sukcesu, instancja_BybitExecutor).
    """
    logger.info("Inicjalizacja usług tradingowych...")
    try:
        if not config.BYBIT_API_KEY or not config.BYBIT_API_SECRET:
            raise ValueError("Klucze API Bybit nie są ustawione w konfiguracji.")
        
        executor_instance = BybitExecutor(
            api_key=config.BYBIT_API_KEY,
            api_secret=config.BYBIT_API_SECRET
        )
        logger.info("BybitExecutor pomyślnie zainicjalizowany.")
        return True, executor_instance
    except (RuntimeError, ValueError) as e:
        logger.critical(f"Nie można zainicjalizować BybitExecutor: {e}. Funkcjonalność handlowa będzie wyłączona.")
        return False, None

def configure_bybit_account(executor: BybitExecutor) -> bool:
    """
    TYMCZASOWA WERSJA DIAGNOSTYCZNA. ZAWSZE ZWRACA TRUE.
    """
    logger.warning("--- [DIAGNOSTYKA] Pomijam konfigurację konta Bybit. Zawsze zwracam sukces. ---")
    return True

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
             logger.error(f"Zatrzymano cykl, aplikacja nie zainicjalizowana. Powód: {reason}", extra={"json_fields": {"cycle_id": cycle_id}})
             return jsonify({"status": "error", "message": f"Service is unhealthy: {reason}"}), 503
        
        # W wersji diagnostycznej, logika bota nie będzie działać poprawnie,
        # ponieważ bybit_executor nie jest ustawiony. To jest OK.
        # Chcemy tylko zobaczyć, czy endpoint zwróci 200 OK.
        logger.warning("[DIAGNOSTYKA] Aplikacja uruchomiona. Logika bota może zgłaszać błędy z powodu braku BybitExecutor.")
        try:
            last_ts = load_last_processed_timestamp()
            new_alerts, new_ts = fetch_new_alerts_since(last_ts)
            if new_alerts:
                logger.info(f"Przetwarzam {len(new_alerts)} nowych alertów.", extra={"json_fields": {"cycle_id": cycle_id}})
                bot_logic_module.process_new_alerts(new_alerts)
                if new_ts and new_ts > last_ts:
                    save_last_processed_timestamp(new_ts)
            
            bot_logic_module.run_trading_logic()

            logger.info("--- ZAKOŃCZENIE CYKLU BOTA ---", extra={"json_fields": {"cycle_id": cycle_id, "status": "success"}})
            return jsonify({"status": "success", "cycle_id": cycle_id}), 200
        except Exception as e:
            logger.error(f"Krytyczny błąd w głównym cyklu bota: {e}", exc_info=True, extra={"json_fields": {"cycle_id": cycle_id, "status": "error"}})
            return jsonify({"status": "error", "message": str(e), "cycle_id": cycle_id}), 500

def initialize_app_services(app: Flask):
    """
    TYMCZASOWA WERSJA DIAGNOSTYCZNA.
    """
    with app.app_context():
        logger.info("Rozpoczynam szybką inicjalizację aplikacji `bot_service`.")
        
        load_config()
        firebase_ok = initialize_firebase()
        bigquery_ok = initialize_bigquery()
        
        # TYMCZASOWO UPROSZCZONA LOGIKA BYBIT
        # Udajemy, że inicjalizacja i konfiguracja Bybit zawsze się udają,
        # ale nie tworzymy instancji egzekutora, aby uniknąć błędów.
        trading_services_ok = True
        bybit_config_ok = True
        
        # Celowo komentujemy blok, który używa Bybit, aby wyizolować problem.
        # executor = None
        # if trading_services_ok:
        #     _, executor = initialize_trading_services()
        #
        # if executor:
        #     app.config['BYBIT_EXECUTOR'] = executor
        #     bot_logic_module.bybit_executor = executor
        #     bybit_config_ok = configure_bybit_account(executor)
        
        if firebase_ok and bigquery_ok and trading_services_ok and bybit_config_ok:
            app.config['INITIALIZATION_SUCCESS'] = True
            logger.info("Wszystkie usługi zainicjalizowane (Bybit pominięty). Aplikacja gotowa do startu.")
        else:
            app.config['INITIALIZATION_SUCCESS'] = False
            reasons = []
            if not firebase_ok: reasons.append("Firebase failed")
            if not bigquery_ok: reasons.append("BigQuery failed")
            final_reason = ", ".join(reasons)
            app.config['INITIALIZATION_FAILURE_REASON'] = final_reason
            logger.critical(f"Krytyczny błąd podczas inicjalizacji. Powód: {final_reason}")