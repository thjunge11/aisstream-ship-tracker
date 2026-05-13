FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ais_producer/ .

CMD ["python", "ais_producer.py"]
