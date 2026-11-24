import re
import unicodedata

MAX_LEN = 1000

BLOCKLIST_PATTERNS = [
    r"ignor[ae]\s+(le\s+)?istruzion",
    r"ignore\s+(previous|all|above)\s+instructions",
    r"disregard\s+(previous|all|above)",
    r"system\s*prompt",
    r"prompt\s+di\s+sistema",
    r"le\s+tue\s+istruzioni",
    r"developer\s+mode",
    r"modalit[aà]\s+svilupp",
    r"jailbreak",
    r"\bdan\b",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in BLOCKLIST_PATTERNS]


def sanitize_input(text: str) -> tuple[bool, str]:
    if text is None or not text.strip():
        return False, "Messaggio vuoto."

    if len(text) > MAX_LEN:
        return False, f"Messaggio troppo lungo (massimo {MAX_LEN} caratteri)."

    normalized = unicodedata.normalize("NFKC", text)
    cleaned = "".join(c for c in normalized if c.isprintable() or c in "\n\t")

    for pattern in _COMPILED:
        if pattern.search(cleaned):
            return False, "Messaggio non consentito dal filtro di sicurezza."

    return True, cleaned
