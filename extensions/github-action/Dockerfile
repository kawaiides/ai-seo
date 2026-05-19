# AEGIS AEO Gate — single-purpose CI image.
# Built on slim-bookworm so the action starts in <2s on a warm runner.

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN pip install --upgrade pip "httpx>=0.27,<0.29"

WORKDIR /opt/aegis
COPY aegis_gate.py /opt/aegis/aegis_gate.py
COPY entrypoint.sh /opt/aegis/entrypoint.sh
RUN chmod +x /opt/aegis/entrypoint.sh

ENTRYPOINT ["/opt/aegis/entrypoint.sh"]
