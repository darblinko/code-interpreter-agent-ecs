# Service image. Contract with infra/: listen on 8080, GET /health, POST /v1/run,
# /workspace and /tmp writable, read-only root filesystem, python on PATH.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/src \
    WORKSPACE_ROOT=/workspace \
    SANDBOX_UID=10002 \
    SANDBOX_GID=10002

# app (10001) runs the API and holds the model keys. sandbox (10002) runs model-written code.
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && groupadd --gid 10002 sandbox && useradd --uid 10002 --gid 10002 --no-create-home --shell /usr/sbin/nologin sandbox \
    && mkdir -p /workspace \
    && chown 10001:10002 /workspace && chmod 2770 /workspace && chmod 1777 /tmp \
    && { find / -xdev -type f \( -perm -4000 -o -perm -2000 \) -exec chmod a-s {} + 2>/dev/null || true; }

WORKDIR /app
COPY service/requirements.txt /app/service/requirements.txt
RUN pip install --no-cache-dir -r /app/service/requirements.txt
COPY src /app/src
COPY service /app/service
RUN python -m compileall -q /app

# ECS copies an image directory into a task volume, with its ownership, only when the volume's
# containerPath matches a VOLUME directive. Without it /workspace would be a root-owned mount.
VOLUME ["/workspace", "/tmp"]
EXPOSE 8080

# The container starts as root only so that setpriv can switch to the app user. It then keeps
# CAP_SETUID, CAP_SETGID and CAP_KILL as ambient capabilities, which the API process needs to
# start code workers as the sandbox user and to kill them on timeout, and drops every other
# capability from the effective, permitted and inheritable sets. The bounding set is limited by
# the task definition, which drops every default capability except these three. Workers are
# started through setpriv again, which clears their inheritable and ambient sets, so model code
# runs with no capabilities. no_new_privs blocks regaining privileges through setuid programs.
ENTRYPOINT ["setpriv", "--reuid=10001", "--regid=10001", "--groups=10002", \
    "--inh-caps=-all,+setuid,+setgid,+kill", "--ambient-caps=-all,+setuid,+setgid,+kill", \
    "--no-new-privs", "--"]
CMD ["python", "-m", "service"]
