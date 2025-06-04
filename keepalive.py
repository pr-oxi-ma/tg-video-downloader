import os
import requests
import time

WEB_URL = os.getenv("RENDER_EXTERNAL_URL")
PING_INTERVAL = 300  # 5 minutes

def ping_server():
    try:
        if WEB_URL:
            response = requests.get(WEB_URL, timeout=10)
            if response.status_code == 200:
                print("Keepalive ping successful")
                return True
        return False
    except Exception as e:
        print(f"Keepalive error: {e}")
        return False

if __name__ == "__main__":
    print("Starting keepalive service...")
    while True:
        ping_server()
        time.sleep(PING_INTERVAL)