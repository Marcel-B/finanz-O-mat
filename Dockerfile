# 1. Oberfläche bauen (Vue + Vite)
FROM node:22-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
# vite schreibt nach ../ustva/static
RUN npm run build

# 2. Python-App mit gebauter Oberfläche
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY ustva ./ustva
COPY --from=frontend /src/ustva/static ./ustva/static
RUN pip install --no-cache-dir .

ENV DATENBANK=/data/finanzomat.sqlite
VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "-m", "ustva", "serve", "--host", "0.0.0.0", "--port", "8000"]
