from google.cloud import firestore

PROJECT_ID = "trading-bot-463318"
COLLECTION_NAME = "bot_config"
DOCUMENT_ID = "instrument_rules"
FIELD_NAME = "rules"

db = firestore.Client(project=PROJECT_ID)
doc_ref = db.collection(COLLECTION_NAME).document(DOCUMENT_ID)

rules_data = {
    "AAVEUSDT": {"qtyStep": "0.01", "tickSize": "0.01"},
    "ADAUSDT": {"qtyStep": "1", "tickSize": "0.0001"},
    "ALGOUSDT": {"qtyStep": "0.1", "tickSize": "0.0001"},
    "APTUSDT": {"qtyStep": "0.01", "tickSize": "0.001"},
    "ARBUSDT": {"qtyStep": "0.1", "tickSize": "0.0001"},
    "ATOMUSDT": {"qtyStep": "0.1", "tickSize": "0.001"},
    "AVAXUSDT": {"qtyStep": "0.1", "tickSize": "0.001"},
    "AXSUSDT": {"qtyStep": "0.1", "tickSize": "0.001"},
    "BCHUSDT": {"qtyStep": "0.01", "tickSize": "0.1"},
    "BNBUSDT": {"qtyStep": "0.1", "tickSize": "0.1"},
    "BTCUSDT": {"qtyStep": "0.001", "tickSize": "0.1"},
    "CROUSDT": {"qtyStep": "1", "tickSize": "0.00001"},
    "DOGEUSDT": {"qtyStep": "1", "tickSize": "0.00001"},
    "DOTUSDT": {"qtyStep": "0.1", "tickSize": "0.0001"},
    "ENAUSDT": {"qtyStep": "1", "tickSize": "0.0001"},
    "ETCUSDT": {"qtyStep": "0.1", "tickSize": "0.001"},
    "ETHUSDT": {"qtyStep": "0.1", "tickSize": "0.01"},
    "FLRUSDT": {"qtyStep": "1", "tickSize": "0.00001"},
    "HBARUSDT": {"qtyStep": "1", "tickSize": "0.00001"},
    "HYPEUSDT": {"qtyStep": "0.01", "tickSize": "0.001"},
    "ICPUSDT": {"qtyStep": "0.1", "tickSize": "0.001"},
    "IMXUSDT": {"qtyStep": "0.1", "tickSize": "0.0001"},
    "INJUSDT": {"qtyStep": "0.1", "tickSize": "0.001"},
    "KASUSDT": {"qtyStep": "1", "tickSize": "0.00001"},
    "LINKUSDT": {"qtyStep": "0.1", "tickSize": "0.001"},
    "LTCUSDT": {"qtyStep": "0.1", "tickSize": "0.01"},
    "MNTUSDT": {"qtyStep": "0.1", "tickSize": "0.0001"},
    "NEARUSDT": {"qtyStep": "0.1", "tickSize": "0.001"},
    "ONDOUSDT": {"qtyStep": "1", "tickSize": "0.0001"},
    "OPUSDT": {"qtyStep": "0.1", "tickSize": "0.0001"},
    "ORDIUSDT": {"qtyStep": "0.01", "tickSize": "0.001"},
    "POLUSDT": {"qtyStep": "1", "tickSize": "0.0001"},
    "PYTHUSDT": {"qtyStep": "1", "tickSize": "0.0001"},
    "RENDERUSDT": {"qtyStep": "0.1", "tickSize": "0.001"},
    "SANDUSDT": {"qtyStep": "1", "tickSize": "0.0001"},
    "SEIUSDT": {"qtyStep": "1", "tickSize": "0.0001"},
    "SOLUSDT": {"qtyStep": "0.1", "tickSize": "0.01"},
    "STXUSDT": {"qtyStep": "0.1", "tickSize": "0.0001"},
    "SUIUSDT": {"qtyStep": "1", "tickSize": "0.0001"},
    "TAOUSDT": {"qtyStep": "0.01", "tickSize": "0.001"},
    "TIAUSDT": {"qtyStep": "0.1", "tickSize": "0.001"},
    "TONUSDT": {"qtyStep": "0.1", "tickSize": "0.0001"},
    "TRXUSDT": {"qtyStep": "1", "tickSize": "0.00001"},
    "UNIUSDT": {"qtyStep": "0.1", "tickSize": "0.001"},
    "WLDUSDT": {"qtyStep": "0.1", "tickSize": "0.0001"},
    "XLMUSDT": {"qtyStep": "1", "tickSize": "0.00001"},
    "XMRUSDT": {"qtyStep": "0.01", "tickSize": "0.01"},
    "XRPUSDT": {"qtyStep": "1", "tickSize": "0.0001"},
    "ZECUSDT": {"qtyStep": "0.01", "tickSize": "0.01"}
}

doc_ref.set({FIELD_NAME: rules_data}, merge=True)
print("DONE — instrument_rules document replaced successfully.")