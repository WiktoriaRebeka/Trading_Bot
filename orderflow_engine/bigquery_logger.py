# orderflow_engine/bigquery_logger.py
# WERSJA 2.0 - Używa istniejących tabel

from google.cloud import bigquery
from datetime import datetime
import logging

from shared_lib import constants

logger = logging.getLogger(__name__)

class OrderFlowBigQueryLogger:
    """
    Logger do BigQuery dla OrderFlow Engine.
    Używa istniejących tabel:
    - market_structure_signals (rozszerzona)
    - liquidation_events (nowa)
    - dom_events (nowa)
    """
    
    def __init__(self, project_id='trading-bot-463318'):
        self.client = bigquery.Client(project=project_id)
        self.dataset = constants.BIGQUERY_DATASET_ID
    
    def log_setup_signal(self, signal_data):
        """
        Zapisuje setup signal do market_structure_signals.
        
        WAŻNE: Używamy istniejącej tabeli, nie tworzymy nowej!
        """
        table_id = f"{self.dataset}.market_structure_signals"
        
        row = {
            # === PODSTAWOWE POLA (ISTNIEJĄCE) ===
            'signal_id': signal_data.get('setup_id'),
            'event_id': signal_data.get('event_id'),
            'symbol': signal_data['symbol'],
            'timestamp': datetime.utcnow(),
            'direction': signal_data['direction'],
            'entry': signal_data['entry'],
            'sl': signal_data['sl'],
            'tp': signal_data['tp'],
            'risk_pct': abs(signal_data['sl'] - signal_data['entry']) / signal_data['entry'] * 100,
            'rr': 3.0,  # Stałe RR (możesz przekazać dynamicznie)
            'structure_state': 1 if signal_data['direction'] == 'LONG' else -1,
            
            # === TIME FEATURES (ISTNIEJĄCE) ===
            'session': self._get_session(datetime.utcnow()),
            'minute_of_day': datetime.utcnow().hour * 60 + datetime.utcnow().minute,
            'day_of_week': datetime.utcnow().weekday(),
            'second': datetime.utcnow().second,
            
            # === DERIVATIVES DATA (ISTNIEJĄCE) ===
            'funding_rate': signal_data.get('funding_rate', 0),
            'open_interest': signal_data.get('open_interest', 0),
            'risk_usdt': signal_data.get('risk_usdt', 10.0),
            
            # === NOWE POLA (ORDER FLOW) ===
            'liquidation_volume_usd': signal_data.get('liq_volume', 0),
            'liquidation_detected': signal_data.get('liq_volume', 0) > 0,
            'delta_divergence': signal_data.get('delta_div', False),
            'delta_strength': signal_data.get('delta_strength', 0),
            'obi_value': signal_data.get('obi', 0),
            'dom_wall_detected': signal_data.get('wall_detected', False),
            'wall_price': signal_data.get('wall_price'),
            'wall_size': signal_data.get('wall_size'),
            'confidence_score': signal_data.get('confidence', 0),
            
            # === PLACEHOLDERS (dla kompatybilności z istniejącą tabelą) ===
            'bos_high': False,
            'bos_low': False,
            'choch_up': False,
            'choch_down': False,
            'liquidity_grab_above': signal_data['direction'] == 'SHORT',
            'liquidity_grab_below': signal_data['direction'] == 'LONG',
            'liquidity_price': signal_data.get('entry'),  # Możesz przekazać dokładny poziom
            'eqh_detected': False,
            'eql_detected': False,
            'bar_range': 0.0,
            'ob_range': abs(signal_data['sl'] - signal_data['entry']),
            'swing_range': 0.0,
            'distance_to_liquidity': 0.0,
            'volatility_regime': 'UNKNOWN',
            'm2_delta': 0.0,
            'm5_rs_ratio': 0.0,
            'raw_context': {}
        }
        
        try:
            errors = self.client.insert_rows_json(table_id, [row])
            if not errors:
                logger.info(f"✅ Setup logged to market_structure_signals: {signal_data['symbol']}")
            else:
                logger.error(f"❌ BigQuery insert errors: {errors}")
        except Exception as e:
            logger.error(f"❌ BigQuery setup log error: {e}", exc_info=True)
    
    def log_liquidation_cascade(self, event_data):
        """Zapisuje liquidation cascade event (NOWA TABELA)"""
        table_id = f"{self.dataset}.liquidation_events"
        
        row = {
            'event_id': event_data.get('event_id'),
            'symbol': event_data['symbol'],
            'cascade_type': event_data['cascade_type'],
            'total_volume_usd': event_data['total_volume_usd'],
            'dominant_volume_usd': event_data.get('dominant_volume_usd', 0),
            'count': event_data['count'],
            'timestamp': datetime.utcnow()
        }
        
        try:
            errors = self.client.insert_rows_json(table_id, [row])
            if not errors:
                logger.info(f"✅ Liquidation event logged: {event_data['symbol']}")
        except Exception as e:
            logger.error(f"❌ BigQuery liq log error: {e}")
    
    def log_dom_wall(self, wall_data):
        """Zapisuje DOM wall event (NOWA TABELA)"""
        table_id = f"{self.dataset}.dom_events"
        
        row = {
            'symbol': wall_data['symbol'],
            'side': wall_data['side'],
            'price': wall_data['price'],
            'size': wall_data['size'],
            'obi': wall_data['obi'],
            'timestamp': datetime.utcnow()
        }
        
        try:
            errors = self.client.insert_rows_json(table_id, [row])
        except Exception as e:
            logger.error(f"❌ DOM event log error: {e}")
    
    def _get_session(self, dt):
        """Określa sesję tradingową na podstawie godziny UTC"""
        hour = dt.hour
        
        if 0 <= hour < 7:
            return "ASIA"
        elif 7 <= hour < 15:
            return "LONDON"
        elif 15 <= hour < 21:
            return "NY"
        else:
            return "AFTERHOURS"