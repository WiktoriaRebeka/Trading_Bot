# Lokalizacja: bot_service/app_setup.py

import logging
import uuid
from flask import Flask, jsonify
from typing import Optional, Tuple

from shared_lib.config_loader import load_config
from shared_lib.firebase_client import initialize_firebase
from shared_lib.config import config 
from bot_service.pnl_logger import initialize_pnl_logger
from bot_service.bot_logic import process_new_alerts, run_trading_logic, sync_pnl_history
from bot_service.fetch_from_firestore import (
    load_last_processed_timestamp, 
    fetch_new_alerts_since, 
    save_last_processed_timestamp
)

logger = logging.getLogger(__name__)

def initialize_trading_services() -> Tuple[bool, Optional['BybitExecutor']]:
    from bot_service.bybit_executor import BybitExecutor
    logger.info("Inicjalizacja usług tradingowych...")
    try:
        api_key = config.BYBIT_API_KEY
        api_secret = config.BYBIT_API_SECRET
        if not api_key or not isinstance(api_key, str) or len(api_key.strip()) == 0:
            logger.critical("KRYTYCZNY BŁĄD KONFIGURACJI: BYBIT_API_KEY jest pusty lub nie został załadowany z Secret Manager.")
            raise ValueError("Klucz API Bybit jest pusty.")
        if not api_secret or not isinstance(api_secret, str) or len(api_secret.strip()) == 0:
            logger.critical("KRYTYCZNY BŁĄD KONFIGURACJI: BYBIT_API_SECRET jest pusty lub nie został załadowany z Secret Manager.")
            raise ValueError("Sekret API Bybit jest pusty.")
        executor_instance = BybitExecutor(
            api_key=api_key,
            api_secret=api_secret
        )
        logger.info("BybitExecutor pomyślnie zainicjalizowany.")
        return True, executor_instance
    except (RuntimeError, ValueError) as e:
        logger.critical(f"Nie można zainicjalizować BybitExecutor: {e}")
        return False, None

def initialize_app_services(app: Flask):
    """Inicjalizuje wszystkie usługi i przechowuje je w kontekście aplikacji."""
    with app.app_context():
        logger.info("Rozpoczynam inicjalizację aplikacji bot_service.")
        load_config()
        
        firebase_ok = initialize_firebase()
        pnl_logger_ok = initialize_pnl_logger()
        trading_services_ok, executor = initialize_trading_services()
        
        if executor:
            app.config['BYBIT_EXECUTOR'] = executor
       
        if firebase_ok and pnl_logger_ok and trading_services_ok:
            app.config['INITIALIZATION_SUCCESS'] = True
            logger.info("Wszystkie kluczowe usługi zainicjalizowane. Aplikacja gotowa do startu.")
        else:
            app.config['INITIALIZATION_SUCCESS'] = False
            reasons = []
            if not firebase_ok: reasons.append("Firebase failed")
            if not pnl_logger_ok: reasons.append("PNL Logger (BigQuery) failed")
            if not trading_services_ok: reasons.append("BybitExecutor failed")
            final_reason = ", ".join(reasons)
            app.config['INITIALIZATION_FAILURE_REASON'] = final_reason
            logger.critical(f"Krytyczny błąd podczas inicjalizacji. Powód: {final_reason}")

def register_endpoints(app: Flask):
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
        
        bybit_executor = app.config.get('BYBIT_EXECUTOR')
        if not bybit_executor:
            logger.error("Krytyczny błąd: BybitExecutor nie jest dostępny w konfiguracji aplikacji.")
            return jsonify({"status": "error", "message": "BybitExecutor not initialized"}), 500

        try:
            # Krok 1: Przetwarzanie nowych alertów (otwieranie nowych pozycji)
            last_ts = load_last_processed_timestamp()
            new_alerts, new_ts = fetch_new_alerts_since(last_ts)
            if new_alerts:
                logger.info(f"Przetwarzam {len(new_alerts)} nowych alertów.", extra={"json_fields": {"cycle_id": cycle_id}})
                process_new_alerts(new_alerts, bybit_executor)
                if new_ts and new_ts > last_ts:
                    save_last_processed_timestamp(new_ts)
            
            # Krok 2: Zarządzanie istniejącymi setupami i pozycjami
            run_trading_logic(bybit_executor)

            # Krok 3: Synchronizacja historii P&L z Bybit (nasz "księgowy")
            sync_pnl_history(bybit_executor)

            logger.info("--- ZAKOŃCZENIE CYKLU BOTA ---", extra={"json_fields": {"cycle_id": cycle_id, "status": "success"}})
            return jsonify({"status": "success", "cycle_id": cycle_id}), 200
        except Exception as e:
            logger.error(f"Krytyczny błąd w głównym cyklu bota: {e}", exc_info=True, extra={"json_fields": {"cycle_id": cycle_id, "status": "error"}})
            return jsonify({"status": "error", "message": str(e), "cycle_id": cycle_id}), 500