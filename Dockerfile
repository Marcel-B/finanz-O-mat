FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY ustva ./ustva
RUN pip install --no-cache-dir .

ENV DATENBANK=/data/finanzomat.sqlite
VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "-m", "ustva", "serve", "--host", "0.0.0.0", "--port", "8000"]
