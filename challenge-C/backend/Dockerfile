FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py documents.py ./

RUN useradd -m -u 1000 backenduser
USER backenduser

EXPOSE 9200
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9200"]
