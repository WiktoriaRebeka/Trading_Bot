import requests

try:
    response = requests.get("https://httpbin.org/get")
    print(f"✅ Requests działa: {response.status_code}")
except Exception as e:
    print(f"❌ Błąd requests: {e}")

