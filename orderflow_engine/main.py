# orderflow_engine/main.py
# WERSJA: 5.0 - Używa MultiConnectionWSManager

import asyncio
import logging
import os
from websocket_handler import MultiConnectionWSManager
from metrics_processor import OrderFlowMetrics
from config_symbols import SYMBOLS_TO_WATCH_CLEAN
from shared_lib.firebase_client import get_firestore_client

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def main():
    """
    Główny entry point OrderFlow Engine V5.0
    """
    logger.info("=" * 60)
    logger.info("🚀 OrderFlow Engine V5.0 - Institutional Footprint Tracker")
    logger.info("=" * 60)
    
    # Inicjalizacja Firestore
    try:
        firestore_client = get_firestore_client()
        logger.info("✅ Firestore połączony")
    except Exception as e:
        logger.error(f"❌ Błąd połączenia z Firestore: {e}")
        firestore_client = None
    
    # Inicjalizacja Metrics Processor
    metrics_processor = OrderFlowMetrics(firestore_client=firestore_client)
    
    # Inicjalizacja WebSocket Manager
    logger.info(f"📡 Inicjalizacja WebSocket dla {len(SYMBOLS_TO_WATCH_CLEAN)} symboli...")
    ws_manager = MultiConnectionWSManager(
        symbols=SYMBOLS_TO_WATCH_CLEAN,
        metrics_processor=metrics_processor
    )
    
    # Uruchom wszystkie połączenia
    try:
        await ws_manager.start_all_connections()
    except KeyboardInterrupt:
        logger.info("⚠️  Otrzymano sygnał zatrzymania...")
        await ws_manager.shutdown()
    except Exception as e:
        logger.error(f"❌ Krytyczny błąd: {e}", exc_info=True)
        await ws_manager.shutdown()

if __name__ == "__main__":
    asyncio.run(main())