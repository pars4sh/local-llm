# arxiv-mcp-server (github.com/blazickjp/arxiv-mcp-server, Apache-2.0) over Streamable HTTP.
# It has no published image, so it is built here: a pinned Python base and every dependency
# pinned in requirements.txt (pip freeze of `pip install arxiv-mcp-server==<version>`).
FROM python:3.13.15-slim-bookworm

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --root-user-action=ignore --no-deps -r /tmp/requirements.txt
# Search results carry each abstract's first two sentences (D-023); fails the build on a mismatch.
COPY patch_snippet.py /tmp/patch_snippet.py
RUN python /tmp/patch_snippet.py /usr/local/lib/python3.13/site-packages/arxiv_mcp_server/tools/search.py \
    && rm /tmp/patch_snippet.py

RUN useradd --create-home --uid 10001 app
USER app

# Downloaded papers live in the container only; they are a cache, not data worth keeping.
ENV TRANSPORT=http HOST=0.0.0.0 PORT=8000 ALLOWED_HOSTS=arxiv,arxiv:8000
EXPOSE 8000
CMD ["arxiv-mcp-server", "--storage-path", "/home/app/papers"]
