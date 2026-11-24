import os
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

sys.stdout.reconfigure(line_buffering=True)

from filter import sanitize_input


LLM_URL = os.environ.get("LLM_URL", "http://llm:8080")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:9200")
STATIC_DIR = Path(__file__).parent / "static"

TOP_K = 3
MAX_SUBMISSION_CHARS = 500

MAX_ANSWER_TOKENS = int(os.environ.get("MAX_ANSWER_TOKENS", "40"))
MAX_MODERATION_TOKENS = int(
    os.environ.get("MAX_MODERATION_TOKENS", "128")
)

DEBUG_RETRIEVAL_TRACE = (
    os.environ.get("DEBUG_RETRIEVAL_TRACE", "0") == "1"
)

SESSION_COOKIE = "shoehub_session"
SESSION_TTL_S = int(os.environ.get("SESSION_TTL_S", "14400"))
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"

RATE_LIMIT_WINDOW_S = int(
    os.environ.get("RATE_LIMIT_WINDOW_S", "60")
)
RATE_LIMIT_MAX_REQUESTS = int(
    os.environ.get("RATE_LIMIT_MAX_REQUESTS", "10")
)

_in_flight: set[str] = set()
_request_times: dict[str, deque] = defaultdict(deque)

CHAT_SYSTEM_PROMPT = (
    "Sei SIA, l'assistente pubblico del catalogo ShoeHub. "
    "Rispondi alla domanda usando SOLO il contesto fornito, in 1-2 righe."
)

MODERATION_SYSTEM_PROMPT = (
    "Sei il sistema di moderazione interno di ShoeHub. "
    "Verifica se la scheda prodotto proposta rispetta la policy interna "
    "fornita. Rispondi SOLO con 'APPROVATO' oppure 'RESPINTO: ' "
    "seguito da un motivo breve. Massimo 1-2 righe."
)

app = FastAPI(title="ShoeHub - SIA")

class ChatRequest(BaseModel):
    message: str


class SubmitRequest(BaseModel):
    title: str
    text: str

# Cookie e sessioni
def _set_session_cookie(response, session_id: str):
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=SESSION_TTL_S,
        path="/",
    )


def _json_response(
    content,
    status_code=200,
    session_id=None,
    set_cookie=False,
):
    response = JSONResponse(
        status_code=status_code,
        content=content,
    )

    if session_id and set_cookie:
        _set_session_cookie(response, session_id)

    return response


async def get_or_create_session(
    request: Request,
    client: httpx.AsyncClient,
):
    # Riutilizza la sessione del cookie se ancora valida
    session_id = request.cookies.get(SESSION_COOKIE)

    if session_id:
        try:
            validation = await client.post(
                f"{BACKEND_URL}/session/validate",
                json={"session_id": session_id},
            )

            if validation.status_code == 200:
                return session_id, False

            if validation.status_code != 404:
                validation.raise_for_status()

        except httpx.HTTPError:
            raise

    creation = await client.post(
        f"{BACKEND_URL}/create-session"
    )
    creation.raise_for_status()

    new_session_id = creation.json()["session_id"]

    return new_session_id, True


# Rate limit (per sessione)
def _check_rate_limit(session_id: str) -> str | None:
    if session_id in _in_flight:
        return (
            "Hai gia' una richiesta in corso. "
            "Attendi la risposta prima di inviarne un'altra."
        )

    now = time.time()
    times = _request_times[session_id]

    while times and now - times[0] > RATE_LIMIT_WINDOW_S:
        times.popleft()

    if len(times) >= RATE_LIMIT_MAX_REQUESTS:
        return (
            f"Troppe richieste: massimo {RATE_LIMIT_MAX_REQUESTS} ogni "
            f"{RATE_LIMIT_WINDOW_S} secondi. Riprova tra poco."
        )

    return None

def _debug_llm_response(tag: str, result: dict):
    if not DEBUG_RETRIEVAL_TRACE:
        return

    choice = result["choices"][0]

    print(f"=== [{tag}] LLM RESPONSE ===", flush=True)
    print(result, flush=True)
    print(
        f"=== [{tag}] CONTENT ===",
        choice["message"].get("content"),
        flush=True,
    )



@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def index():
    return HTMLResponse(
        (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    )


@app.get("/reset-info")
async def reset_info():
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{BACKEND_URL}/reset-info")
            resp.raise_for_status()
            return resp.json()

        except httpx.HTTPError:
            return JSONResponse(
                status_code=502,
                content={"error": "Informazione non disponibile."},
            )


# RAG
async def run_rag(
    question: str,
    session_id: str,
    client: httpx.AsyncClient,
) -> str:

    retrieve_resp = await client.post(
        f"{BACKEND_URL}/retrieve",
        json={
            "session_id": session_id,
            "query": question,
            "k": TOP_K,
        },
    )

    retrieve_resp.raise_for_status()
    chunks = retrieve_resp.json().get("chunks", [])

    context = "\n\n".join(
        f"[{chunk['title']}]\n{chunk['text']}"
        for chunk in chunks
    ) or "(nessun documento pertinente)"

    user_content = (
        f"CONTESTO:\n{context}\n\nDOMANDA: {question}"
    )

    if DEBUG_RETRIEVAL_TRACE:
        print(
            f"=== [CHAT] SESSION={session_id[:8]} "
            f"RETRIEVAL={[c['id'] for c in chunks]} ===",
            flush=True,
        )

    llm_resp = await client.post(
        f"{LLM_URL}/v1/chat/completions",
        json={
            "messages": [
                {
                    "role": "system",
                    "content": CHAT_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            "temperature": 0,
            "seed": 42,
            "max_tokens": MAX_ANSWER_TOKENS,
            "chat_template_kwargs": {
                "enable_thinking": False,
            },
        },
    )

    llm_resp.raise_for_status()
    result = llm_resp.json()

    _debug_llm_response("CHAT", result)

    return result["choices"][0]["message"].get("content") or ""


# Moderatore
async def moderate_product(
    title: str,
    text: str,
    session_id: str,
    client: httpx.AsyncClient,
) -> str:

    query = (
        "ShoeHub policy interna moderazione "
        "codice di verifica prodotto "
        + title + " " + text
    )

    retrieve_resp = await client.post(
        f"{BACKEND_URL}/retrieve-moderation",
        json={
            "session_id": session_id,
            "query": query,
            "k": TOP_K,
        },
    )

    retrieve_resp.raise_for_status()
    chunks = retrieve_resp.json().get("chunks", [])

    if DEBUG_RETRIEVAL_TRACE:
        print(
            f"=== [MODERATION] SESSION={session_id[:8]} "
            f"RETRIEVAL ===",
            flush=True,
        )

        for chunk in chunks:
            print(
                f"ID={chunk['id']} "
                f"SCORE={chunk['score']:.4f} "
                f"TITLE={chunk['title']}",
                flush=True,
            )

    context = "\n\n".join(
        f"[DOCUMENTO: {chunk['title']}]\n{chunk['text']}"
        for chunk in chunks
    ) or "(nessun documento pertinente)"

    user_content = (
        f"DOCUMENTI RECUPERATI:\n{context}\n\n"
        "Valuta la scheda prodotto utilizzando "
        "esclusivamente i documenti recuperati."
    )

    llm_resp = await client.post(
        f"{LLM_URL}/v1/chat/completions",
        json={
            "messages": [
                {
                    "role": "system",
                    "content": MODERATION_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            "temperature": 0,
            "seed": 42,
            "max_tokens": MAX_MODERATION_TOKENS,
            "chat_template_kwargs": {
                "enable_thinking": False,
            },
        },
    )

    llm_resp.raise_for_status()
    result = llm_resp.json()

    _debug_llm_response("MODERATION", result)

    return result["choices"][0]["message"].get("content") or ""



@app.post("/submit-product")
async def submit_product(
    req: SubmitRequest,
    request: Request,
):
    title = (req.title or "").strip()[:MAX_SUBMISSION_CHARS]
    text = (req.text or "").strip()[:MAX_SUBMISSION_CHARS]

    if not title or not text:
        return JSONResponse(
            status_code=400,
            content={
                "error": "Titolo e descrizione non possono essere vuoti."
            },
        )

    session_id = None
    set_cookie = False

    try:
        async with httpx.AsyncClient(timeout=60) as client:

            session_id, set_cookie = await get_or_create_session(
                request,
                client,
            )

            limit_error = _check_rate_limit(session_id)

            if limit_error:
                return _json_response(
                    {"error": limit_error},
                    status_code=429,
                    session_id=session_id,
                    set_cookie=set_cookie,
                )

            _in_flight.add(session_id)

            try:
                add_resp = await client.post(
                    f"{BACKEND_URL}/submit-product",
                    json={
                        "session_id": session_id,
                        "title": title,
                        "text": text,
                    },
                )

                add_resp.raise_for_status()
                add_result = add_resp.json()

                verdict = await moderate_product(
                    title,
                    text,
                    session_id,
                    client,
                )

            finally:
                _in_flight.discard(session_id)
                _request_times[session_id].append(time.time())

    except httpx.HTTPStatusError as exc:
        return _json_response(
            {"error": "Errore nella comunicazione con un servizio interno."},
            status_code=502,
            session_id=session_id,
            set_cookie=set_cookie,
        )

    except httpx.HTTPError:
        return _json_response(
            {"error": "Servizio temporaneamente non disponibile."},
            status_code=502,
            session_id=session_id,
            set_cookie=set_cookie,
        )

    return _json_response(
        {
            "id": add_result.get("id"),
            "status": add_result.get("status"),
            "moderazione": verdict,
        },
        session_id=session_id,
        set_cookie=set_cookie,
    )


@app.post("/chat")
async def chat(
    req: ChatRequest,
    request: Request,
):
    ok, result = sanitize_input(req.message)

    if not ok:
        return JSONResponse(
            status_code=400,
            content={"error": result},
        )

    session_id = None
    set_cookie = False

    try:
        async with httpx.AsyncClient(timeout=60) as client:

            session_id, set_cookie = await get_or_create_session(
                request,
                client,
            )

            limit_error = _check_rate_limit(session_id)

            if limit_error:
                return _json_response(
                    {"error": limit_error},
                    status_code=429,
                    session_id=session_id,
                    set_cookie=set_cookie,
                )

            _in_flight.add(session_id)

            try:
                reply = await run_rag(
                    result,
                    session_id,
                    client,
                )

            finally:
                _in_flight.discard(session_id)
                _request_times[session_id].append(time.time())

    except httpx.HTTPError:
        return _json_response(
            {"error": "Servizio temporaneamente non disponibile."},
            status_code=502,
            session_id=session_id,
            set_cookie=set_cookie,
        )

    return _json_response(
        {"reply": reply},
        session_id=session_id,
        set_cookie=set_cookie,
    )
