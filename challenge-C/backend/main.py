
import asyncio
import os
import secrets
import threading
import time
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from documents import FIXED_DOCUMENTS, RESERVED_POLICY


app = FastAPI(title="ShoeHub RAG Backend")

MAX_SUBMISSION_CHARS = 500
MAX_RETRIEVAL_K = 10

RESET_INTERVAL_S = max(
    60, int(os.environ.get("CATALOG_RESET_INTERVAL_S", "900"))
)
SESSION_TTL_S = max(
    300, int(os.environ.get("SESSION_TTL_S", "14400"))
)

_sessions = {}
_sessions_lock = threading.Lock()
_last_reset_time = time.time()



def _build_index(documents):
    #Costruisce un indice TF-IDF a partire dai documenti ricevuti
    if not documents:
        raise ValueError("Impossibile creare un indice vuoto.")

    texts = [
        (doc["title"] + ". " + doc["text"]).strip()
        for doc in documents
    ]

    vectorizer = TfidfVectorizer()
    matrix = vectorizer.fit_transform(texts)

    return vectorizer, matrix


def _rebuild_catalog_index(session):
    vectorizer, matrix = _build_index(session["catalog_corpus"])
    session["catalog_vectorizer"] = vectorizer
    session["catalog_matrix"] = matrix


def _rebuild_moderation_index(session):
    vectorizer, matrix = _build_index(session["moderation_corpus"])
    session["moderation_vectorizer"] = vectorizer
    session["moderation_matrix"] = matrix

# Sessioni
def _create_session():

    session_id = secrets.token_urlsafe(32)

    session = {
        "catalog_corpus": [
            dict(document) for document in FIXED_DOCUMENTS
        ],
        "moderation_corpus": [
            {
                "id": "reserved-policy",
                "title": "Policy interna ShoeHub",
                "text": RESERVED_POLICY["text"],
            }
        ],
        "catalog_vectorizer": None,
        "catalog_matrix": None,
        "moderation_vectorizer": None,
        "moderation_matrix": None,
        "last_activity": time.time(),
        "lock": threading.RLock(),
    }

    _rebuild_catalog_index(session)
    _rebuild_moderation_index(session)

    with _sessions_lock:
        _sessions[session_id] = session

    return session_id


@contextmanager
def _locked_session(session_id):
    with _sessions_lock:
        session = _sessions.get(session_id)

        if session is None:
            raise HTTPException(
                status_code=404,
                detail="Sessione non valida o scaduta."
            )

        session["lock"].acquire()

    try:
        session["last_activity"] = time.time()
        yield session
    finally:
        session["lock"].release()


def _reset_session_catalog(session):
    # Reset del solo catalogo personale

    with session["lock"]:
        session["catalog_corpus"] = [
            dict(document) for document in FIXED_DOCUMENTS
        ]
        _rebuild_catalog_index(session)


def _reset_all_catalogs():
    global _last_reset_time

    with _sessions_lock:
        sessions = list(_sessions.values())

    for session in sessions:
        _reset_session_catalog(session)

    _last_reset_time = time.time()


async def _reset_loop():
    while True:
        await asyncio.sleep(RESET_INTERVAL_S)
        _reset_all_catalogs()


async def _cleanup_sessions_loop():
    while True:
        await asyncio.sleep(300)

        now = time.time()

        with _sessions_lock:
            expired = []

            for session_id, session in list(_sessions.items()):
                with session["lock"]:
                    inactive = (
                        now - session["last_activity"] > SESSION_TTL_S
                    )

                if inactive:
                    expired.append(session_id)

            for session_id in expired:
                _sessions.pop(session_id, None)


@app.on_event("startup")
async def start_background_tasks():
    asyncio.create_task(_reset_loop())
    asyncio.create_task(_cleanup_sessions_loop())


class SessionRequest(BaseModel):
    session_id: str

class SubmitRequest(BaseModel):
    session_id: str
    title: str
    text: str


class RetrieveRequest(BaseModel):
    session_id: str
    query: str
    k: int = Field(default=3, ge=1, le=MAX_RETRIEVAL_K)


class ModerationRetrieveRequest(BaseModel):
    session_id: str
    query: str
    k: int = Field(default=3, ge=1, le=MAX_RETRIEVAL_K)


# Endpoint
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/create-session")
async def create_session():
    session_id = _create_session()
    return {"session_id": session_id}

@app.post("/session/validate")
async def validate_session(req: SessionRequest):
    with _locked_session(req.session_id):
        return {"valid": True}

@app.get("/reset-info")
async def reset_info():
    elapsed = time.time() - _last_reset_time
    remaining = max(0, RESET_INTERVAL_S - elapsed)

    return {
        "next_reset_in_s": int(remaining),
        "interval_s": RESET_INTERVAL_S,
    }


@app.post("/submit-product")
async def submit_product(req: SubmitRequest):
    title = (req.title or "").strip()[:MAX_SUBMISSION_CHARS]
    text = (req.text or "").strip()[:MAX_SUBMISSION_CHARS]

    if not title or not text:
        raise HTTPException(
            status_code=400,
            detail="Titolo e descrizione non possono essere vuoti."
        )

    with _locked_session(req.session_id) as session:
        moderation_existing = next(
            (
                doc for doc in session["moderation_corpus"]
                if doc["id"] != "reserved-policy"
                and doc["title"] == title
                and doc["text"] == text
            ),
            None
        )

        if moderation_existing:
            document = dict(moderation_existing)
            status = "documento già presente"
        else:
            count = sum(
                1 for doc in session["moderation_corpus"]
                if doc["id"] != "reserved-policy"
            )

            document = {
                "id": f"submitted-{count + 1}",
                "title": title,
                "text": text,
            }

            session["moderation_corpus"].append(dict(document))
            _rebuild_moderation_index(session)
            status = "aggiunto al catalogo personale"

        catalog_existing = any(
            doc["title"] == title and doc["text"] == text
            for doc in session["catalog_corpus"]
        )

        if not catalog_existing:
            session["catalog_corpus"].append(dict(document))
            _rebuild_catalog_index(session)

    return {
        "id": document["id"],
        "status": status,
    }


# Ricerca nel catalogo personale
def _retrieve_from_index(query, k, corpus, vectorizer, matrix):
    query = (query or "").strip()

    if not query:
        raise HTTPException(
            status_code=400,
            detail="La query non può essere vuota."
        )

    query_vector = vectorizer.transform([query])
    similarities = cosine_similarity(query_vector, matrix)[0]

    ranked = sorted(
        enumerate(similarities),
        key=lambda item: item[1],
        reverse=True,
    )

    chunks = []

    for index, score in ranked[:k]:
        if score <= 0:
            continue

        document = corpus[index]

        chunks.append({
            "id": document["id"],
            "title": document["title"],
            "text": document["text"],
            "score": float(score),
        })

    return chunks


# Ricerca
@app.post("/retrieve")
async def retrieve(req: RetrieveRequest):
    with _locked_session(req.session_id) as session:
        chunks = _retrieve_from_index(
            req.query,
            req.k,
            session["catalog_corpus"],
            session["catalog_vectorizer"],
            session["catalog_matrix"],
        )

    return {"chunks": chunks}


@app.post("/retrieve-moderation")
async def retrieve_moderation(req: ModerationRetrieveRequest):
    with _locked_session(req.session_id) as session:
        chunks = _retrieve_from_index(
            req.query,
            req.k,
            session["moderation_corpus"],
            session["moderation_vectorizer"],
            session["moderation_matrix"],
        )

    return {"chunks": chunks}
