import asyncio
import json
import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

RENDER_TIMEOUT_S = float(os.environ.get("RENDER_TIMEOUT_S", "20"))

WORKER_PATH = os.path.join(os.path.dirname(__file__), "render_worker.py")

app = FastAPI(title="Mind ReportEngine")


class RenderRequest(BaseModel):
    markdown: str


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/render")
async def render(req: RenderRequest):
    payload = json.dumps({"markdown": req.markdown}).encode("utf-8")

    proc = await asyncio.create_subprocess_exec(
        "python3",
        WORKER_PATH,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(payload), timeout=RENDER_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return JSONResponse(
            status_code=504,
            content={
                "error": (
                    f"Generazione del PDF interrotta: ha superato il tempo "
                    f"massimo consentito ({RENDER_TIMEOUT_S:.0f}s)."
                )
            },
        )

    if proc.returncode != 0:
        err_msg = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
        return JSONResponse(
            status_code=500,
            content={"error": f"Errore durante la generazione del PDF: {err_msg}"},
        )

    return Response(content=stdout, media_type="application/pdf")
