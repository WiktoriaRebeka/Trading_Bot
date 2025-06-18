TRADING_BOT/
├── .env                    # Local environment variables (in .gitignore)
├── .gcloudignore             # Files to ignore for App Engine deployment
├── .gitignore                # Files to ignore for Git
├── app.yaml                  # App Engine configuration (incl. env variables)
├── requirements.txt          # Python dependencies
│
├── app/                      # Main application source code
│   ├── __init__.py
│   ├── bot_logic.py          # Core trading decision logic
│   ├── constants.py            # Application constants
│   ├── fetch_from_firestore.py # Logic for fetching alerts
│   ├── firebase_client.py      # Firebase Admin SDK initialization
│   ├── main.py                 # Flask app & main coordinator
│   ├── positions_logger.py     # Logs trade results to Firestore
│   └── state_manager.py        # Manages the in-memory state of active OB setups