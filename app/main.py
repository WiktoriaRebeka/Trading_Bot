# TRADING_BOT/app/main.py (TYMCZASOWY PLIK DO TESTU URUCHOMIENIA)
import time
import os
import sys
import traceback # Dodaj ten import

print(f"[MAIN_SIMPLIFIED_TEST] Uruchamianie uproszczonego main.py w App Engine. Wersja testowa: 1.0") # Dodaj wersję testową dla łatwiejszej identyfikacji
print(f"[MAIN_SIMPLIFIED_TEST] GAE_INSTANCE: {os.getenv('GAE_INSTANCE')}")
print(f"[MAIN_SIMPLIFIED_TEST] GAE_ENV: {os.getenv('GAE_ENV')}")
print(f"[MAIN_SIMPLIFIED_TEST] Python version: {sys.version}")
print(f"[MAIN_SIMPLIFIED_TEST] Bieżący katalog: {os.getcwd()}")

# Sprawdźmy, czy katalog 'app' jest widoczny z perspektywy main.py uruchamianego jako moduł
# Jeśli entrypoint to "python -m app.main", to cwd powinno być katalogiem nadrzędnym 'app' (czyli TRADING_BOT)
# a pliki z 'app' powinny być dostępne przez 'app.nazwa_pliku'
# Jednak dla os.listdir('.') zobaczymy zawartość TRADING_BOT
# Spróbujmy wylistować zawartość katalogu, w którym jest ten plik (app/)
current_file_dir = os.path.dirname(os.path.abspath(__file__))
print(f"[MAIN_SIMPLIFIED_TEST] Katalog pliku main.py: {current_file_dir}")
if os.path.exists(current_file_dir) and os.path.isdir(current_file_dir):
    print(f"[MAIN_SIMPLIFIED_TEST] Zawartość katalogu '{current_file_dir}': {os.listdir(current_file_dir)}")
else:
    print(f"[MAIN_SIMPLIFIED_TEST] Nie można wylistować katalogu '{current_file_dir}'.")


# Spróbuj zaimportować coś, co może być problematyczne, np. Firebase
try:
    print("[MAIN_SIMPLIFIED_TEST] Próba importu firebase_admin...")
    import firebase_admin
    from firebase_admin import credentials # Dodaj import credentials
    from firebase_admin import firestore
    print("[MAIN_SIMPLIFIED_TEST] firebase_admin zaimportowany pomyślnie.")
    
    # Minimalna inicjalizacja Firebase (bez credentials, polega na domyślnych w App Engine)
    if not firebase_admin._apps:
        print("[MAIN_SIMPLIFIED_TEST] Inicjalizacja Firebase Admin SDK...")
        # W App Engine Standard, initialize_app() bez argumentów powinno działać
        firebase_admin.initialize_app() 
        db_test = firestore.client()
        print("[MAIN_SIMPLIFIED_TEST] Firebase Admin SDK zainicjowane, klient Firestore uzyskany.")
        
        # Prosta operacja na Firestore (opcjonalnie, tylko do testu)
        print("[MAIN_SIMPLIFIED_TEST] Próba testowego zapisu do Firestore...")
        doc_ref = db_test.collection("startup_tests").document("simplified_main_test_v1_0") # Użyj unikalnej nazwy dokumentu
        doc_ref.set({
            "timestamp": firestore.SERVER_TIMESTAMP, 
            "message": "Uproszczony main.py działa! Wersja testowa 1.0",
            "gae_instance": os.getenv('GAE_INSTANCE', 'N/A')
        })
        print("[MAIN_SIMPLIFIED_TEST] Testowy zapis do Firestore wykonany.")
    else:
        print("[MAIN_SIMPLIFIED_TEST] Firebase Admin SDK było już zainicjowane.")

except ImportError as e_imp:
    print(f"[MAIN_SIMPLIFIED_TEST_ERROR] Błąd importu: {e_imp}")
    traceback.print_exc()
except Exception as e_init:
    print(f"[MAIN_SIMPLIFIED_TEST_ERROR] Błąd podczas inicjalizacji lub testu Firebase: {type(e_init).__name__} - {e_init}")
    traceback.print_exc()

print(f"[MAIN_SIMPLIFIED_TEST] Uproszczony main.py pozostanie aktywny i będzie logował co minutę.")

count = 0
while True: # Pętla, aby utrzymać proces przy życiu
   count += 1
   print(f"[MAIN_SIMPLIFIED_TEST] Pętla podtrzymująca ({count}). Instancja działa. Czas: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
   time.sleep(60) # Loguj co 60 sekund