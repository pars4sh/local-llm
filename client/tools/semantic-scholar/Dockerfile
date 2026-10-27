# Semantic Scholar MCP server (server.py, D-016): pinned base and every dependency pinned in
# requirements.txt.
FROM python:3.13.15-slim-bookworm

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --root-user-action=ignore --no-deps -r /tmp/requirements.txt

RUN useradd --create-home --uid 10001 app
USER app
WORKDIR /home/app
COPY server.py .

EXPOSE 8000
CMD ["python", "server.py"]
