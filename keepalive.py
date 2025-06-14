import os
import requests
import time
import logging

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

WEB_URL = os.getenv("RENDER_EXTERNAL_URL")
PING_INTERVAL = 300  # 5 minutes

def ping_server():
    try:
        if WEB_URL:
            response = requests.get(WEB_URL, timeout=10)
            if response.status_code == 200:
                logger.info("Keepalive ping successful")
                return True
        return False
    except Exception as e:
        logger.error(f"Keepalive error: {e}")
        return False

if __name__ == "__main__":
    logger.info("Starting keepalive service...")
    while True:
        ping_server()
        time.sleep(PING_INTERVAL)