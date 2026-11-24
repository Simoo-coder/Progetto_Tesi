FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py read_worker.py ./
COPY flag.txt /flag.txt

# Cartella "legittima" per gli allegati, pensata come unico posto in cui
# attachment_path dovrebbe poter puntare (ma che l'app non verifica mai:
# e' questa l'assenza di controllo alla base della vulnerabilita').
COPY uploads /uploads

RUN useradd -m -u 1000 backenduser \
    && chown backenduser:backenduser /flag.txt \
    && chmod 400 /flag.txt \
    && chown -R backenduser:backenduser /uploads

USER backenduser

EXPOSE 9100
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9100"]
