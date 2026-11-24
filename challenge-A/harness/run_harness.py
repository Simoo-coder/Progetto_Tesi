import io
import re
import sys
import time

import requests
from pypdf import PdfReader

sys.path.insert(0, "../solution")
from payload import PROMPT

API_URL = "http://localhost:8000/report"
FLAG_PATTERN = re.compile(r"FLAG\{[^}]+\}")


RATE_LIMIT_RETRY_WAIT_S = 7
MAX_RATE_LIMIT_RETRIES = 10


def one_attempt() -> tuple[bool, float]:
    t0 = time.time()

    for _ in range(MAX_RATE_LIMIT_RETRIES):
        try:
            resp = requests.post(API_URL, json={"prompt": PROMPT}, timeout=120)
        except requests.RequestException:
            return False, time.time() - t0

        if resp.status_code == 429:
            time.sleep(RATE_LIMIT_RETRY_WAIT_S)
            continue
        break
    else:
        return False, time.time() - t0

    elapsed = time.time() - t0
    if resp.status_code != 200:
        return False, elapsed

    try:
        reader = PdfReader(io.BytesIO(resp.content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return False, elapsed

    return bool(FLAG_PATTERN.search(text)), elapsed


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    successes = 0
    times = []

    for i in range(1, n + 1):
        ok, elapsed = one_attempt()
        successes += ok
        times.append(elapsed)
        print(f"[{i}/{n}] {'OK' if ok else 'FAIL'}  ({elapsed:.1f}s)")

    rate = successes / n * 100
    avg_time = sum(times) / len(times) if times else 0

    print("\n== RISULTATO ==")
    print(f"Tentativi: {n}")
    print(f"Successi: {successes} ({rate:.1f}%)")
    print(f"Tempo medio per tentativo: {avg_time:.1f}s")


if __name__ == "__main__":
    main()
