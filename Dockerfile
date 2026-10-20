FROM python:3.12.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATABASE=/data/sales.sqlite
WORKDIR /srv/app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock \
    && groupadd --gid 10001 sales \
    && useradd --uid 10001 --gid sales --no-create-home sales \
    && mkdir /data && chown sales:sales /data
COPY app ./app
COPY data ./data
COPY tools ./tools
USER 10001:10001
EXPOSE 8000
# Exactly one process owns the bounded in-memory limiter; threads share its lock.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--worker-tmp-dir", "/tmp", "--timeout", "30", "--access-logfile", "/dev/null", "app:create_app()"]
