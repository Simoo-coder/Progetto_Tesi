FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libffi-dev \
    shared-mime-info \
    fonts-liberation \
    libgdk-pixbuf-2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY render_worker.py .

COPY flag.txt /flag.txt

RUN useradd -m -u 1000 rendereruser \
    && chown root:rendereruser /flag.txt \
    && chmod 440 /flag.txt \
    && test -s /flag.txt

USER 1000:1000

CMD ["python3", "/app/render_worker.py"]