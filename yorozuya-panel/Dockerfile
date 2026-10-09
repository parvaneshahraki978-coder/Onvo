FROM python:3.12-slim

WORKDIR /app

# System deps for pillow/qrcode (tiny)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo libpng16-16 \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render injects $PORT. DATA_DIR=/data when a disk is attached.
ENV PORT=10000
EXPOSE 10000

CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
