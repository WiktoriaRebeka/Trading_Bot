# Lokalizacja: bot_service/app_setup.py
import asyncio
import logging
import os
import uuid
from flask import Flask, jsonify, request

from shared_lib.secret_manager import get_secret
from shared_lib.firebase_client import initialize_firebase
from bot_service.bigquery_logger import initialize_bigquery
from bot_service.bybit_executor import BybitExecutor
from bot_service.bot_logic import handle_immediate_signal, log_closed_positions_pnl, update_filled_orders
from shared_lib.constants import ORDERFLOW_ENGINE_URL
from shared_lib.signal_mode import get_signal_mode, is_msi_orderblock_mode

# NEW imports for OrderFlow async client
from bot_service.orderflow_client import AsyncOrderFlowClient, SyncOrderFlowAdapter

logger = logging.getLogger(__name__)

GCP_PROJECT_ID = os.getenv("GCP_PROJECT")
USE_TESTNET = os.getenv("USE_TESTNET", "true").lower() == "true"

def register_endpoints(app: Flask):
    @app.before_request
    def log_request_info():
        if request.path in ("/health", "/"):
            logger.debug(
                "Health probe: path=%s method=%s",
                request.path,
                request.method,
            )
            return
        safe_headers = {str(k): str(v) for k, v in request.headers.items() if k.lower() not in ['authorization', 'cookie']}
        logger.info(
            f"--- OTRZYMANO ŻĄDANIE --- Endpoint: {request.path}, Metoda: {request.method}",
            extra={"json_fields": {"path": request.path, "method": request.method, "headers": safe_headers}}
        )

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

    @app.route('/process-alerts', methods=['POST'])
    def process_alerts_endpoint():
        alert_payload = request.get_json()
        if not alert_payload:
            return jsonify({"status": "error", "message": "No payload received"}), 400

        if not app.config.get('INITIALIZATION_SUCCESS', False):
            return jsonify({"status": "error", "message": "Service initializing"}), 503

        try:
            executor = app.config.get('BYBIT_EXECUTOR')
            asyncio.run(handle_immediate_signal(alert_payload, executor))
            return jsonify({"status": "success"}), 200
        except Exception as e:
            logger.error(f"Błąd endpointu: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route('/log-pnl', methods=['POST'])
    def log_pnl_endpoint():
        cycle_id = str(uuid.uuid4())
        logger.info(f"--- Rozpoczynam cykl logowania PnL [ID: {cycle_id}] ---")

        if not app.config.get('INITIALIZATION_SUCCESS', False):
            reason = app.config.get('INITIALIZATION_FAILURE_REASON', 'Aplikacja niezainicjalizowana.')
            logger.error(f"Zatrzymano cykl PnL, ponieważ aplikacja nie jest 'healthy'. Powód: {reason}", extra={"json_fields": {"cycle_id": cycle_id}})
            return jsonify({"status": "error", "message": f"Service is unhealthy: {reason}"}), 503

        try:
            bybit_executor = app.config.get('BYBIT_EXECUTOR')
            if not bybit_executor:
                raise RuntimeError("BybitExecutor nie został poprawnie zainicjalizowany.")
            processed_count = log_closed_positions_pnl(bybit_executor)
            logger.info(f"--- Cykl logowania PnL zakończony. Przetworzono {processed_count} rekordów. [ID: {cycle_id}] ---")
            return jsonify({"status": "success", "processed_records": processed_count, "cycle_id": cycle_id}), 200
        except Exception as e:
            logger.error(f"KRYTYCZNY BŁĄD w cyklu logowania PnL: {e}", exc_info=True, extra={"json_fields": {"cycle_id": cycle_id}})
            return jsonify({"status": "error", "message": str(e), "cycle_id": cycle_id}), 500

    @app.route('/update-orders', methods=['POST'])
    def update_orders_endpoint():
        cycle_id = str(uuid.uuid4())
        logger.info(f"--- Rozpoczynam cykl aktualizacji zleceń [ID: {cycle_id}] ---")

        if not app.config.get('INITIALIZATION_SUCCESS', False):
            reason = app.config.get('INITIALIZATION_FAILURE_REASON', 'Aplikacja niezainicjalizowana.')
            logger.error(f"Zatrzymano cykl aktualizacji, ponieważ aplikacja nie jest 'healthy'. Powód: {reason}", extra={"json_fields": {"cycle_id": cycle_id}})
            return jsonify({"status": "error", "message": f"Service is unhealthy: {reason}"}), 503

        try:
            bybit_executor = app.config.get('BYBIT_EXECUTOR')
            if not bybit_executor:
                raise RuntimeError("BybitExecutor nie został poprawnie zainicjalizowany.")
            update_filled_orders(bybit_executor)
            logger.info(f"--- Cykl aktualizacji zleceń zakończony pomyślnie [ID: {cycle_id}] ---")
            return jsonify({"status": "success", "cycle_id": cycle_id}), 200
        except Exception as e:
            logger.error(f"KRYTYCZNY BŁĄD w cyklu aktualizacji zleceń: {e}", exc_info=True, extra={"json_fields": {"cycle_id": cycle_id}})
            return jsonify({"status": "error", "message": str(e), "cycle_id": cycle_id}), 500


def initialize_app_services(app: Flask):
    with app.app_context():
        logger.info("Rozpoczynam konfigurację aplikacji bot_service.")

        failure_reasons = []

# V8.0: BigQuery nie jest krytyczne dla HEALTH CHECKU (nie blokuje startu)
        if not initialize_firebase(): 
            failure_reasons.append("Failed to initialize Firebase/Firestore")
        
        # Próbujemy zainicjalizować BQ, ale to nie jest krytyczny błąd startowy.
        if not initialize_bigquery():
             logger.warning("BigQuery initialization failed. Reporting will be skipped, but trading core proceeds.")
        if not GCP_PROJECT_ID:
            failure_reasons.append("Zmienna środowiskowa GCP_PROJECT nie jest ustawiona.")
        else:
            api_key = get_secret("bybit-api-key", GCP_PROJECT_ID)
            api_secret = get_secret("bybit-api-secret", GCP_PROJECT_ID)

            if api_key and api_secret:
                try:
                    executor = BybitExecutor(api_key=api_key, api_secret=api_secret, testnet=USE_TESTNET)
                    app.config['BYBIT_EXECUTOR'] = executor
                    logger.info(f"BybitExecutor pomyślnie zainicjalizowany. Tryb Testnet: {USE_TESTNET}")
                    logger.info(
                        "SIGNAL_MODE=%s | MSI orders=%s",
                        get_signal_mode(),
                        is_msi_orderblock_mode(),
                    )

                    # --- NOWY KROK: Inicjalizacja Async OrderFlow Client ---
                    orderflow_url = os.getenv("ORDERFLOW_ENGINE_URL", ORDERFLOW_ENGINE_URL)
                    if orderflow_url:
                        async_client = AsyncOrderFlowClient(orderflow_url, timeout_s=0.5, max_retries=2)
                        # create sync adapter for use in synchronous handlers
                        sync_adapter = SyncOrderFlowAdapter(async_client)
                        app.config['ORDERFLOW_CLIENT'] = sync_adapter
                        # store async client and loop for graceful shutdown if needed
                        app.config['ORDERFLOW_ASYNC_CLIENT'] = async_client
                        logger.info(f"OrderFlow Client (sync adapter) zainicjalizowany z URL: {orderflow_url}")
                    else:
                        logger.warning("ORDERFLOW_ENGINE_URL not set; OrderFlow client not initialized.")

                except Exception as e:
                    failure_reasons.append(f"Błąd inicjalizacji BybitExecutor/OrderFlow: {e}")
            else:
                failure_reasons.append("Nie udało się pobrać kluczy API z Secret Manager.")

        if not failure_reasons:
            app.config['INITIALIZATION_SUCCESS'] = True
            logger.info("Aplikacja Flask [bot_service] została pomyślnie utworzona i skonfigurowana.")
        else:
            app.config['INITIALIZATION_SUCCESS'] = False
            final_reason = " & ".join(failure_reasons)
            app.config['INITIALIZATION_FAILURE_REASON'] = final_reason
            logger.critical(f"Krytyczny błąd podczas inicjalizacji. Aplikacja będzie zwracać błędy 503. Powód: {final_reason}")

    # Register teardown to close async client gracefully when the process exits
    @app.teardown_appcontext
    def _shutdown_orderflow_client(exception=None):
        try:
            async_client = app.config.get('ORDERFLOW_ASYNC_CLIENT')
            if async_client:
                # attempt to close gracefully
                try:
                    # if running in an event loop, schedule close; otherwise run directly
                    loop = None
                    try:
                        import asyncio
                        loop = asyncio.get_event_loop()
                    except Exception:
                        loop = None

                    if loop and loop.is_running():
                        # schedule close in running loop
                        asyncio.run_coroutine_threadsafe(async_client.close(), loop)
                    else:
                        # run close synchronously
                        import asyncio
                        asyncio.run(async_client.close())
                except Exception:
                    logger.exception("Failed to close OrderFlow async client cleanly")
        except Exception:
            logger.exception("Error during ORDERFLOW client shutdown hook")