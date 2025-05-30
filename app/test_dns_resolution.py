import socket

hostname = "cceowvpxutqxnaypnopr.supabase.co"
print(f"Attempting to resolve: {hostname}")

try:
    ip_address = socket.gethostbyname(hostname)
    print(f"SUCCESS: Resolved {hostname} to IP address: {ip_address}")
except socket.gaierror as e:
    print(f"FAILURE: Could not resolve {hostname}. Error: {e}")
except Exception as e:
    print(f"UNEXPECTED ERROR: {e}")