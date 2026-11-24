import sys

import requests

from payload import PROMPT

API_URL = "http://localhost:8000/report"


def main():
    print("Invio il prompt di attacco all'API...")
    resp = requests.post(API_URL, json={"prompt": PROMPT}, timeout=120)

    if resp.status_code != 200:
        print(f"Richiesta fallita: {resp.status_code} {resp.text}")
        sys.exit(1)

    with open("report.pdf", "wb") as f:
        f.write(resp.content)

    print("PDF salvato in report.pdf.")

if __name__ == "__main__":
    main()
