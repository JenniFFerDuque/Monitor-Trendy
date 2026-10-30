FROM python:3.12-slim
WORKDIR /app
ENV DATA_DIR=/data PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
# - exec: uvicorn queda como proceso principal y recibe la señal de apagado (guarda lo pendiente).
# - 1 worker: el monitor vive dentro del proceso; dos duplicarían los avisos.
# - sin access log: el panel en vivo generaría ruido en los logs de Railway.
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log --timeout-graceful-shutdown 10"]
