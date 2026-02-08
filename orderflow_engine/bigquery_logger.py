# orderflow_engine/bigquery_logger.py

from google.cloud import bigquery
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class OrderFlowBigQueryLogger:
    """
    Logger do BigQuery dla OrderFlow Engine.
    Zapisuje: Setup Signals, Liquidation Events, DOM Events.
    """
    
    def __init__(self, project_id='trading-bot-463318'):
        self.client = bigquery.Client(project=project_id)
        self.dataset = 'trading_data'
    
    def log_setup_signal(self, signal_data):
        """Zapisuje setup signal do BigQuery"""
        table_id = f"{self.dataset}.setup_signals"
        
        row = {
            'setup_id': signal_data.get('setup_id'),
            'event_id': signal_data.get('event_id'),
            'symbol': signal_data['symbol'],
            'direction': signal_data['direction'],
            'entry_price': signal_data['entry'],
            'sl_price': signal_data['sl'],
            'tp_price': signal_data['tp'],
            
            # Confidence components
            'liquidation_volume_usd': signal_data.get('liq_volume', 0),
            'delta_divergence': signal_data.get('delta_div', False),
            'delta_strength': signal_data.get('delta_strength', 0),
            'obi_value': signal_data.get('obi', 0),
            'dom_wall_detected': signal_data.get('wall_detected', False),
            'funding_rate': signal_data.get('funding_rate', 0),
            
            # Computed score
            'confidence_score': signal_data.get('confidence', 0),
            
            'timestamp': datetime.utcnow()
        }
        
        try:
            errors = self.client.insert_rows_json(table_id, [row])
            if not errors:
                logger.info(f"✅ Setup logged to BigQuery: {signal_data['symbol']}")
            else:
                logger.error(f"❌ BigQuery insert errors: {errors}")
        except Exception as e:
            logger.error(f"❌ BigQuery setup log error: {e}", exc_info=True)
    
    def log_liquidation_cascade(self, event_data):
        """Zapisuje liquidation cascade event"""
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
        """Zapisuje DOM wall event"""
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