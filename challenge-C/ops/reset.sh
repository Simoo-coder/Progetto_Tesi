set -euo pipefail

COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$COMPOSE_DIR"

echo "[$(date -Iseconds)] Riavvio Challenge C..."
docker compose restart
echo "[$(date -Iseconds)] Riavvio completato."
