# The MCP server in a container, for directories that build and check servers
# (Glama). Day to day, run `codexwatch mcp` on your own machine instead: a
# container cannot see your Codex logs unless you mount them, for example
#   docker run -i --rm -v ~/.codex:/root/.codex:ro codexwatch
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY codexwatch ./codexwatch
RUN pip install --no-cache-dir . && useradd --create-home codexwatch
USER codexwatch
ENTRYPOINT ["codexwatch", "mcp"]
