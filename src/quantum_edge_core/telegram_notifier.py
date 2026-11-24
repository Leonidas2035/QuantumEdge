import os
import sys
import requests
from dotenv import load_dotenv

# Load env variables
load_dotenv('/home/layman/.hermes/.env')

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8290294960:AAGO2Vp9JREhSddRHz-1Ef4cog4quhDx8RM")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")

def send_telegram_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the configured Telegram channel/chat."""
    if not CHANNEL_ID:
        print("Error: TELEGRAM_CHANNEL_ID is not configured in .env", file=sys.stderr)
        return False
        
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": parse_mode
    }
    
    try:
        r = requests.post(url, json=payload, timeout=15)
        res = r.json()
        if not res.get("ok"):
            print(f"Telegram API Error: {res.get('description')}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"Failed to send Telegram message: {e}", file=sys.stderr)
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 telegram_notifier.py <message>", file=sys.stderr)
        sys.exit(1)
        
    msg = sys.argv[1]
    success = send_telegram_message(msg)
    sys.exit(0 if success else 1)
