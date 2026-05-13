FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY position_history_consumer/ .

CMD ["python", "position_history_consumer.py"]
