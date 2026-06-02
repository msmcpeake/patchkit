FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY static/ static/

RUN useradd --uid 1000 --home-dir /app --shell /usr/sbin/nologin patchkit \
    && mkdir -p /app/.ssh \
    && chown -R patchkit:patchkit /app

USER patchkit

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
