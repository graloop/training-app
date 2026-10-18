FROM python:3.11-slim

# tzdata: the server's "today" follows TZ (set in docker-compose.yml), so a sync
# after 8 pm in Montreal doesn't already think it's tomorrow. No compiler is
# needed: psycopg2-binary ships ready-built.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
# pip downloads every package from files.pythonhosted.org. A DNS ad blocker
# (Pi-hole) that answers 0.0.0.0 for it makes pip fail with only "exit code: 1"
# in Portainer, so check it first: Portainer then shows this command, and its
# message names the cause. See docs/TROUBLESHOOTING.md.
RUN python -c "import socket, sys; sys.exit(socket.gethostbyname('files.pythonhosted.org').startswith('0.') and 'files.pythonhosted.org is blocked by the DNS (Pi-hole?): allow it, see docs/TROUBLESHOOTING.md')"
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY frontend ./frontend

# Not root: the app only needs to read its own files.
RUN useradd --system --no-create-home trainer
USER trainer

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
