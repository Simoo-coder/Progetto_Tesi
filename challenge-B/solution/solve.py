import sys
import requests

from payload import PROMPT

API_URL = "http://localhost:8000/chat"

def main():
    print("Invio il messaggio di attacco all'assistente...")
    resp = requests.post(API_URL, json={"message": PROMPT}, timeout=120)

    if resp.status_code != 200:
        print(f"Richiesta fallita: {resp.status_code} {resp.text}")
        sys.exit(1)

    reply = resp.json().get("reply", "")
    print("--- Risposta dell'assistente ---")
    print(reply)


if __name__ == "__main__":
    main()
