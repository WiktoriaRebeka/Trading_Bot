# TRADING_BOT/hello_app.py
import time
import os
import sys

print(f"[HELLO_APP_TEST] Hello from hello_app.py in App Engine! Version 2.0")
print(f"[HELLO_APP_TEST] GAE_INSTANCE: {os.getenv('GAE_INSTANCE')}")
print(f"[HELLO_APP_TEST] GAE_ENV: {os.getenv('GAE_ENV')}")
print(f"[HELLO_APP_TEST] Python version: {sys.version}")

count = 0
while True:
    count += 1
    print(f"[HELLO_APP_TEST] Loop ({count}). Instance is alive. Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    time.sleep(60)