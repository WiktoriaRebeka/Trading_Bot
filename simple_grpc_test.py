import sys
import time
import os

# Bez żadnego dodatkowego logowania, tylko print() na stdout
# App Engine powinien to przechwycić

print(f"--- SIMPLE_TEST_SCRIPT.PY START --- {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
sys.stdout.flush() # Wymuś opróżnienie bufora

print(f"Python version: {sys.version}")
sys.stdout.flush()

print(f"GAE_ENV: {os.getenv('GAE_ENV')}")
sys.stdout.flush()

# Spróbujmy czegoś, co mogłoby rzucić błąd, jeśli środowisko jest bardzo ograniczone
try:
    # Utwórz i zapisz coś do pliku tymczasowego (choć App Engine Standard ma ograniczony zapis)
    # To bardziej test, czy proces Pythona w ogóle działa i może wykonywać operacje
    # W App Engine Standard /tmp jest jedynym zapisywalnym miejscem w pamięci.
    temp_file_path = "/tmp/app_engine_test.txt"
    with open(temp_file_path, "w") as f:
        f.write(f"Test log from simple_test_script.py at {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
    print(f"Successfully wrote to {temp_file_path}")
    sys.stdout.flush()

    # Przeczytaj z pliku
    # with open(temp_file_path, "r") as f:
    #     content = f.read()
    # print(f"Content from {temp_file_path}: {content.strip()}")
    # sys.stdout.flush()

except Exception as e:
    print(f"ERROR in simple_test_script.py: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc(file=sys.stdout) # Wyślij traceback na stdout
    sys.stdout.flush()

print(f"--- SIMPLE_TEST_SCRIPT.PY END --- {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
sys.stdout.flush()

# Skrypt się zakończy. Jeśli min_instances=1, instancja powinna pozostać,
# ale ten skrypt wykona się tylko raz przy starcie instancji.
# Możesz dodać pętlę, żeby trwał dłużej, ale to nie jest celem tego testu.
# time.sleep(300) # Opcjonalne, żeby instancja nie zakończyła się od razu (jeśli entrypoint to proces)