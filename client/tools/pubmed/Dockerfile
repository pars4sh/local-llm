# cyanheads/pubmed-mcp-server (Apache-2.0, D-016) with a build-time patch (D-023): search results
# carry the first two sentences of each abstract, or "abstract: not available". patch.mjs fails
# the build if the pinned version's code doesn't match what it expects.
FROM ghcr.io/cyanheads/pubmed-mcp-server:2.10.22

USER root
COPY patch.mjs /tmp/patch.mjs
RUN node /tmp/patch.mjs /usr/src/app/dist/mcp-server/tools/definitions && rm /tmp/patch.mjs
USER bun
