import asyncio
import json
import logging
import threading
import time
import uuid
import base64
import os
import hmac

from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

import docker
from docker.errors import DockerException, NotFound

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

RENDER_IMAGE = "mind-renderer:local"

RENDER_TIMEOUT_S = int(os.getenv("RENDER_TIMEOUT_S", "20"))

MAX_CONCURRENT_JOBS = 2
MAX_MARKDOWN_LENGTH = 12000
MAX_PDF_BYTES = 10 * 1024 * 1024

POLL_INTERVAL_S = 0.2

JOB_LABEL = "mind.role"
JOB_LABEL_VALUE = "renderer-job"

logger = logging.getLogger("mind-supervisor")
logging.basicConfig(level=logging.INFO)

docker_client = docker.from_env(timeout=5)

executor = ThreadPoolExecutor(
    max_workers=MAX_CONCURRENT_JOBS,
    thread_name_prefix="renderer-job"
)

job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

active_containers = {}
active_lock = threading.Lock()

SUPERVISOR_TOKEN = os.environ["SUPERVISOR_TOKEN"]

if len(SUPERVISOR_TOKEN) < 32:
    raise RuntimeError(
        "SUPERVISOR_TOKEN deve contenere almeno 32 caratteri."
    )

class RenderTimeout(Exception):
    pass


class RenderJobError(Exception):
    pass

class RenderRequest(BaseModel):
    markdown: str = Field(max_length=MAX_MARKDOWN_LENGTH)

# Registro container
def register_container(job_id, container):
    with active_lock:
        active_containers[job_id] = container


def unregister_container(job_id):
    with active_lock:
        active_containers.pop(job_id, None)

def remove_container(container) -> bool:
    if container is None:
        return True

    try:
        container.remove(force=True, v=True)

        logger.info(
            "Container %s rimosso correttamente.",
            getattr(container, "short_id", "unknown")
        )

        return True

    except NotFound:
        return True

    except DockerException:
        logger.exception(
            "Impossibile rimuovere il container %s",
            getattr(container, "short_id", "unknown")
        )

        return False


def cleanup_orphaned_jobs():
    try:
        containers = docker_client.containers.list(
            all=True,
            filters={
                "label": f"{JOB_LABEL}={JOB_LABEL_VALUE}"
            }
        )

        for container in containers:
            logger.warning(
                "Rimozione job abbandonato: %s",
                container.short_id
            )

            remove_container(container)

    except DockerException:
        logger.exception("Cleanup iniziale fallito.")
        raise


def cleanup_active_jobs():
    with active_lock:
        jobs = list(active_containers.items())

    for job_id, container in jobs:
        removed = remove_container(container)

        if removed:
            unregister_container(job_id)

            logger.info(
                "Cleanup shutdown completato per job %s",
                job_id
            )
        else:
            logger.error(
                "Cleanup shutdown fallito per job %s",
                job_id
            )


# Lettura pdf
def read_pdf_from_container(container):
    command = [
        "python3",
        "-c",
        (
            "import os, sys, base64; "
            "p='/tmp/report.pdf'; "
            "sys.exit(3) if not os.path.isfile(p) else "
            "sys.exit(4) if os.path.getsize(p) > 10485760 else "
            "print(base64.b64encode(open(p, 'rb').read()).decode())"
        )
    ]

    result = container.exec_run(
        command,
        stdout=True,
        stderr=True
    )

    if result.exit_code == 3:
        return None

    if result.exit_code == 4:
        raise RenderJobError(
            "Il PDF supera la dimensione massima consentita."
        )

    if result.exit_code != 0:
        output = result.output.decode(
            "utf-8",
            errors="replace"
        )

        raise RenderJobError(
            "Errore durante il recupero del PDF: " + output
        )

    try:
        pdf_bytes = base64.b64decode(
            result.output.strip(),
            validate=True
        )
    except Exception as exc:
        raise RenderJobError(
            "Il PDF restituito non è codificato correttamente."
        ) from exc

    if not pdf_bytes.startswith(b"%PDF-"):
        raise RenderJobError(
            "Il file recuperato non è un PDF valido."
        )

    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise RenderJobError(
            "Il PDF supera la dimensione massima consentita."
        )

    return pdf_bytes


# Esecuzione job
def execute_render_job(markdown_text: str) -> bytes:
    job_id = str(uuid.uuid4())
    container = None

    deadline = time.monotonic() + RENDER_TIMEOUT_S

    payload = json.dumps({
        "markdown": markdown_text
    }).encode("utf-8")

    payload_b64 = base64.b64encode(payload).decode("ascii")

    try:
        container = docker_client.containers.create(
            image=RENDER_IMAGE,

            command=[
                "python3",
                "/app/render_worker.py",
                payload_b64
            ],

            stdin_open=False,
            detach=True,
            tty=False,

            read_only=True,
            network_mode="none",
            user="1000:1000",

            cap_drop=["ALL"],
            security_opt=[
                "no-new-privileges:true"
            ],

            pids_limit=64,
            mem_limit="512m",
            nano_cpus=1_000_000_000,

            tmpfs={
                "/tmp": "rw,nosuid,nodev,noexec,size=64m"
            },

            environment={
                "HOME": "/tmp",
                "XDG_CACHE_HOME": "/tmp",
                "PYTHONDONTWRITEBYTECODE": "1"
            },

            labels={
                JOB_LABEL: JOB_LABEL_VALUE,
                "mind.job-id": job_id
            },

            init=True
        )

        register_container(job_id, container)

        logger.info(
            "Avvio job %s, container %s",
            job_id,
            container.short_id
        )

        container.start()

        while True:
            remaining = deadline - time.monotonic()

            if remaining <= 0:
                raise RenderTimeout(
                    "Tempo massimo di rendering superato."
                )

            container.reload()

            state = container.attrs.get("State", {})
            status = state.get("Status")

            # Se il worker è terminato prima che il PDF
            # sia stato recuperato, registra i log.
            if status in ("exited", "dead"):
                logs = container.logs(
                    stdout=True,
                    stderr=True,
                    tail=50
                )

                logger.error(
                    "Worker terminato prima del recupero PDF. "
                    "Job: %s, exit code: %s, log: %s",
                    job_id,
                    state.get("ExitCode", -1),
                    logs.decode("utf-8", errors="replace")
                )

                raise RenderJobError(
                    "Il renderer è terminato prima del recupero PDF."
                )

            pdf_bytes = read_pdf_from_container(container)

            if pdf_bytes is not None:
                logger.info(
                    "PDF recuperato correttamente per job %s. "
                    "Dimensione: %s byte",
                    job_id,
                    len(pdf_bytes)
                )

                return pdf_bytes

            # serve a far attendere il Supervisor tra un controllo e il successivo, 
            # evitando che continui a interrogare Docker senza interruzioni.
            time.sleep(
                min(POLL_INTERVAL_S, remaining)
            )

    except (DockerException, RenderTimeout, RenderJobError):
        raise

    except Exception as exc:
        logger.exception(
            "Errore imprevisto nel job %s",
            job_id
        )

        raise RenderJobError(
            "Errore imprevisto durante il rendering."
        ) from exc

    finally:
        if container is not None:
            removed = remove_container(container)

            if removed:
                unregister_container(job_id)

                logger.info(
                    "Cleanup completato per job %s",
                    job_id
                )
            else:
                logger.error(
                    "Cleanup NON completato per job %s.",
                    job_id
                )


# Ciclo di vita del Supervisor
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Avvio Mind Render Supervisor")

    await asyncio.to_thread(docker_client.ping)

    await asyncio.to_thread(
        cleanup_orphaned_jobs
    )

    try:
        yield

    finally:
        logger.info("Arresto Mind Render Supervisor")

        await asyncio.to_thread(
            cleanup_active_jobs
        )

        await asyncio.to_thread(
            executor.shutdown,
            wait=True,
            cancel_futures=True
        )


app = FastAPI(
    title="Mind Render Supervisor",
    lifespan=lifespan
)

@app.get("/health")
async def health():
    try:
        await asyncio.to_thread(
            docker_client.ping
        )

        return {
            "status": "ok",
            "docker": "connected"
        }

    except DockerException:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "docker": "unavailable"
            }
        )


# Rendering
@app.post("/render")
async def render(
    req: RenderRequest,
    authorization: str | None = Header(default=None)
):
    # costruisce il valore atteso dell'header Authorization
    expected = f"Bearer {SUPERVISOR_TOKEN}"

    if (
        authorization is None
        or not hmac.compare_digest(
            authorization,
            expected
        )
    ):
        return JSONResponse(
            status_code=401,
            content={
                "error": "Autenticazione non valida."
            },
            headers={
                "WWW-Authenticate": "Bearer"
            }
        )

    if not req.markdown.strip():
        return JSONResponse(
            status_code=400,
            content={
                "error": "Markdown vuoto."
            }
        )

    try:
        await asyncio.wait_for(
            job_semaphore.acquire(),
            timeout=0.1
        )

    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=429,
            content={
                "error": "Tutti i renderer sono occupati."
            }
        )

    loop = asyncio.get_running_loop()

    try:
        # Avvio del rendering in un thread separato
        future = loop.run_in_executor(
            executor,
            execute_render_job,
            req.markdown
        )

        # libera uno slot di rendering quando il lavoro termina.
        def release_slot(_):
            try:
                loop.call_soon_threadsafe(
                    job_semaphore.release
                )
            except RuntimeError:
                pass

        future.add_done_callback(release_slot)

        pdf_bytes = await asyncio.shield(future)

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition":
                    "attachment; filename=report.pdf"
            }
        )

    except RenderTimeout:
        logger.warning(
            "Timeout durante il rendering."
        )

        return JSONResponse(
            status_code=504,
            content={
                "error": "Tempo massimo di rendering superato."
            }
        )

    except (DockerException, RenderJobError):
        logger.exception(
            "Errore durante il rendering."
        )

        return JSONResponse(
            status_code=500,
            content={
                "error": "Errore durante la generazione del PDF."
            }
        )

    except Exception:
        logger.exception(
            "Errore imprevisto nel supervisor."
        )

        return JSONResponse(
            status_code=500,
            content={
                "error": "Errore interno del supervisor."
            }
        )