FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SKILLSENTRA_HOST=0.0.0.0 \
    SKILLSENTRA_PORT=8766 \
    SKILLSENTRA_AUTH_MODE=hybrid \
    SKILLSENTRA_DATABASE_PATH=/var/lib/skillsentra/skillsentra.db \
    SKILLSENTRA_ARTIFACT_DIR=/var/lib/skillsentra/artifacts

RUN groupadd --system skillsentra && useradd --system --gid skillsentra --home /app skillsentra
WORKDIR /app

COPY --chown=skillsentra:skillsentra app ./app
COPY --chown=skillsentra:skillsentra demo-apple ./demo-apple
COPY --chown=skillsentra:skillsentra sample-skills ./sample-skills
COPY --chown=skillsentra:skillsentra scripts ./scripts
COPY --chown=skillsentra:skillsentra README.md DEVELOPMENT.md ./

RUN mkdir -p /var/lib/skillsentra/artifacts && chown -R skillsentra:skillsentra /var/lib/skillsentra
USER skillsentra

EXPOSE 8766
VOLUME ["/var/lib/skillsentra"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:8766/api/health',timeout=3)); assert data['data']['status']=='ok'"

CMD ["python", "-m", "app.server", "--host", "0.0.0.0", "--port", "8766"]
