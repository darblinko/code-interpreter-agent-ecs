# PLACEHOLDER owned by the application half of this repo. It exists so that
# `cdk synth` can resolve the image asset. Replace it with the real image.
# Contract: listen on 8080, GET /health, POST /v1/run, run as a non-root user,
# pre-create /workspace and /tmp owned by that user (Fargate ephemeral volumes
# inherit the ownership of an existing image directory).
FROM python:3.12-slim
RUN useradd --uid 10001 --create-home app \
    && mkdir -p /workspace && chown app:app /workspace && chmod 1777 /tmp
USER app
EXPOSE 8080
CMD ["python", "-m", "http.server", "8080"]
