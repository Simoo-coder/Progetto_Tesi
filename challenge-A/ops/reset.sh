# Ripristina periodicamente la Challenge A allo stato iniziale, riavviando
# tutti i servizi

set -euo pipefail

COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$COMPOSE_DIR"

echo "[$(date -Iseconds)] Riavvio Challenge A..."
docker compose restart
echo "[$(date -Iseconds)] Riavvio completato."
