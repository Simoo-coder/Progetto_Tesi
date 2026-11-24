FROM python:3.11-slim

# Dipendenze di sistema richieste da WeasyPrint (Pango/Cairo per il layout
# e il rendering del PDF). L'elenco esatto dei pacchetti puo' variare tra le
# versioni della distribuzione: verificare al primo build e correggere se
# WeasyPrint segnala librerie mancanti all'avvio.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libffi-dev \
    shared-mime-info \
    fonts-liberation \
    && (apt-get install -y --no-install-recommends libgdk-pixbuf-2.0-0 \
        || apt-get install -y --no-install-recommends libgdk-pixbuf2.0-0) \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py render_worker.py ./
COPY flag.txt /flag.txt

# Utente non privilegiato, proprietario del solo file che gli serve.
RUN useradd -m -u 1000 rendereruser \
    && chown rendereruser:rendereruser /flag.txt \
    && chmod 400 /flag.txt

USER rendereruser

EXPOSE 9000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]
