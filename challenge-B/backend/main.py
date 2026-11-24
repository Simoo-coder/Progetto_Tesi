import asyncio
import itertools
import json
import os
import time
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Octopus Helpdesk Backend")

_ticket_id_counter = itertools.count(1)
_tickets: dict[int, dict] = {}

RESET_INTERVAL_S = int(os.environ.get("TICKET_RESET_INTERVAL_S", "900"))  # Reset periodico ogni 15 minuti
_last_reset_time = time.time()


def _reset_tickets():
    global _ticket_id_counter, _last_reset_time
    _tickets.clear()
    _ticket_id_counter = itertools.count(1)
    _last_reset_time = time.time()


async def _reset_loop():
    while True:
        await asyncio.sleep(RESET_INTERVAL_S)
        _reset_tickets()


@app.on_event("startup")
async def _start_reset_loop():
    asyncio.create_task(_reset_loop())


@app.get("/reset-info")
async def reset_info():
    elapsed = time.time() - _last_reset_time
    remaining = max(0, RESET_INTERVAL_S - elapsed)
    return {"next_reset_in_s": int(remaining), "interval_s": RESET_INTERVAL_S}

MAX_ATTACHMENT_CHARS = 4000
READ_TIMEOUT_S = 5.0 # limite sulla lettura dell'allegato

READ_WORKER_PATH = os.path.join(os.path.dirname(__file__), "read_worker.py")


class AttachmentReadError(Exception):
    pass


async def read_attachment(path: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "python3",
        READ_WORKER_PATH,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    payload = json.dumps({"path": path}).encode("utf-8")

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(payload), timeout=READ_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise

    data = json.loads(stdout.decode("utf-8", errors="replace"))
    if "error" in data:
        raise AttachmentReadError(data["error"])
    return data["content"]


class CreateTicketRequest(BaseModel):
    subject: str
    description: str
    attachment_path: Optional[str] = None


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/tickets")
async def create_ticket(req: CreateTicketRequest):
    ticket_id = next(_ticket_id_counter)
    _tickets[ticket_id] = {
        "subject": req.subject,
        "description": req.description,
        "attachment_path": req.attachment_path,
    }
    return {"ticket_id": ticket_id}


@app.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: int):
    ticket = _tickets.get(ticket_id)
    if ticket is None:
        return {"error": f"Ticket {ticket_id} non trovato."}

    result = {
        "ticket_id": ticket_id,
        "subject": ticket["subject"],
        "description": ticket["description"],
        "attachment_path": ticket["attachment_path"],
        "attachment_content": None,
        "attachment_error": None,
    }

    path = ticket["attachment_path"]
    if path:
        try:
            content = await read_attachment(path)
            result["attachment_content"] = content[:MAX_ATTACHMENT_CHARS]
        except asyncio.TimeoutError:
            result["attachment_error"] = (
                f"Lettura dell'allegato interrotta: ha superato il tempo "
                f"massimo consentito ({READ_TIMEOUT_S:.0f}s)."
            )
        except Exception as e:
            result["attachment_error"] = f"Impossibile leggere l'allegato: {e}"

    return result
