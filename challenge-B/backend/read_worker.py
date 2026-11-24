import json
import os
import sys

MAX_ATTACHMENT_BYTES = 16000

_APP_DIR = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))

def _is_app_source_path(path: str) -> bool:
    try:
        resolved = os.path.realpath(path)
    except OSError:
        return False
    return resolved == _APP_DIR or resolved.startswith(_APP_DIR + os.sep)


def main():
    raw = sys.stdin.read()
    try:
        req = json.loads(raw)
        path = req["path"]
    except (json.JSONDecodeError, KeyError) as e:
        print(json.dumps({"error": f"Richiesta non valida: {e}"}))
        sys.exit(1)

    if _is_app_source_path(path):
        print(json.dumps({
            "error": (
                "Percorso non consentito: non e' possibile leggere i file "
                "dell'applicazione stessa."
            )
        }))
        sys.exit(1)

    try:
        with open(path, "rb") as f:
            data = f.read(MAX_ATTACHMENT_BYTES)
        content = data.decode("utf-8", errors="replace")
    except Exception as e:
        print(json.dumps({"error": f"Impossibile leggere l'allegato: {e}"}))
        sys.exit(1)

    print(json.dumps({"content": content}))
    sys.exit(0)


if __name__ == "__main__":
    main()
