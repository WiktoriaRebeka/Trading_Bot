import requests
import json
from datetime import datetime

URL = "https://ekoenergiadomowa.com/webhook_sqlite.php"

test_payload = {
    "symbol": "TESTCOINUSDT.P",
    "event": "TOP_GREEN_CHANGE",
    "value": 123.45,
    "timestamp": datetime.utcnow().isoformat(),
    "source": "WebhookTest"
}

headers = {
    "Content-Type": "application/json"
}

response = requests.post(URL, headers=headers, data=json.dumps(test_payload))

print(f"Status code: {response.status_code}")
print("Response body:", response.text)
