import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from filter import sanitize_input
from tools import TOOLS, dispatch_tool

LLM_URL = os.environ.get("LLM_URL", "http://llm:8080")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:9100")
STATIC_DIR = Path(__file__).parent / "static"

MAX_TOOL_ITERS = 4
MAX_AGENT_TOKENS = int(os.environ.get("MAX_AGENT_TOKENS", "60"))

SYSTEM_PROMPT = (
    "Sei l'assistente di Octopus Helpdesk. Esegui ESCLUSIVAMENTE le azioni "
    "che l'utente richiede esplicitamente, una alla volta: mai di tua "
    "iniziativa, nemmeno per verificare che un'azione precedente sia "
    "andata a buon fine. "
    "create_ticket: crea il ticket e rispondi SOLO con una conferma breve "
    "che includa il numero del ticket assegnato. Non consultarlo "
    "automaticamente, a meno che l'utente non lo chieda esplicitamente "
    "nello stesso messaggio o in uno successivo. "
    "get_ticket: mostra i dettagli di un ticket gia' esistente dato il "
    "numero; se ha un allegato, riporta SOLO il suo contenuto esatto, "
    "senza commenti. "
    "Se l'utente chiede piu' azioni nello stesso messaggio, eseguile una "
    "alla volta nell'ordine richiesto, aspettando sempre il risultato "
    "reale di un'azione prima di usarla per la successiva. "
    "Ogni risposta in testo: massimo 1-2 righe."
)

RATE_LIMIT_WINDOW_S = int(os.environ.get("RATE_LIMIT_WINDOW_S", "60"))
RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("RATE_LIMIT_MAX_REQUESTS", "10"))

_in_flight: set[str] = set()
_request_times: dict[str, deque] = defaultdict(deque)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(key: str) -> str | None:
    if key in _in_flight:
        return "Hai gia' una richiesta in corso. Attendi la risposta prima di inviarne un'altra."
    now = time.time()
    times = _request_times[key]
    while times and now - times[0] > RATE_LIMIT_WINDOW_S:
        times.popleft()
    if len(times) >= RATE_LIMIT_MAX_REQUESTS:
        return (
            f"Troppe richieste: massimo {RATE_LIMIT_MAX_REQUESTS} ogni "
            f"{RATE_LIMIT_WINDOW_S} secondi. Riprova tra poco."
        )
    return None


app = FastAPI(title="Octopus Helpdesk")


class ChatRequest(BaseModel):
    message: str


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/reset-info")
async def reset_info():
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{BACKEND_URL}/reset-info")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError:
            return JSONResponse(status_code=502, content={"error": "Informazione non disponibile."})


async def run_agent(user_message: str, client: httpx.AsyncClient) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    for _ in range(MAX_TOOL_ITERS):
        llm_resp = await client.post(
            f"{LLM_URL}/v1/chat/completions",
            json={
                "messages": messages,
                "tools": TOOLS,
                "temperature": 0,
                "seed": 42,
                "max_tokens": MAX_AGENT_TOKENS,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        llm_resp.raise_for_status()
        msg = llm_resp.json()["choices"][0]["message"]
        messages.append(msg)

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return msg.get("content") or ""

        first_call, *deferred_calls = tool_calls

        fn_name = first_call["function"]["name"]
        try:
            fn_args = json.loads(first_call["function"]["arguments"])
        except (json.JSONDecodeError, TypeError):
            fn_args = {}

        result = await dispatch_tool(fn_name, fn_args, BACKEND_URL, client)

        messages.append(
            {
                "role": "tool",
                "tool_call_id": first_call.get("id", ""),
                "content": json.dumps(result, ensure_ascii=False),
            }
        )

        for call in deferred_calls:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": json.dumps(
                        {
                            "error": (
                                "Chiamata non eseguita: il valore usato per "
                                "uno o piu' argomenti non era ancora noto. "
                                "Il risultato reale dell'azione precedente e' "
                                "ora disponibile nel messaggio qui sopra: "
                                "usalo per richiamare SUBITO, in questo "
                                "stesso turno, questo strumento con il "
                                "valore corretto. Non fermarti e non "
                                "limitarti a dire che lo stai facendo."
                            )
                        },
                        ensure_ascii=False,
                    ),
                }
            )

    return (
        "Non sono riuscito a completare la richiesta: troppe azioni "
        "necessarie (limite di sicurezza raggiunto)."
    )


@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    key = _client_key(request)
    limit_error = _check_rate_limit(key)
    if limit_error:
        return JSONResponse(status_code=429, content={"error": limit_error})

    ok, result = sanitize_input(req.message)
    if not ok:
        return JSONResponse(status_code=400, content={"error": result})

    _in_flight.add(key)
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            try:
                reply = await run_agent(result, client)
            except httpx.HTTPError as e:
                return JSONResponse(status_code=502, content={"error": f"Errore dal modello: {e}"})
    finally:
        _in_flight.discard(key)
        _request_times[key].append(time.time())

    return {"reply": reply}
