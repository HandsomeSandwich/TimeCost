FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# Dinaro runs as its own app via the standalone WSGI entrypoint.
CMD ["gunicorn", "dinaro.wsgi:app", "--workers", "2", "--bind", "0.0.0.0:8080"]
