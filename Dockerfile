# Imagen oficial de Playwright: trae Chromium y sus dependencias del sistema.
# La versión DEBE coincidir con playwright==X en requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --break-system-packages -r requirements.txt

COPY app ./app
COPY alembic.ini ./
COPY migraciones ./migraciones

# pwuser ya existe en la imagen; no correr Chromium como root.
RUN mkdir -p /tmp/facturacion_jobs && chown pwuser /tmp/facturacion_jobs
USER pwuser

# Con DATABASE_URL se aplican las migraciones antes de arrancar; si fallan, el deploy falla
# y Render mantiene la versión anterior. UN solo worker: los trabajos viven en memoria.
CMD if [ -n "$DATABASE_URL" ]; then alembic upgrade head || exit 1; fi; \
    exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --proxy-headers --forwarded-allow-ips='*'
