# The MCP server in a container, for directories that build and check servers
# (Glama). Day to day, run `codexwatch mcp` on your own machine instead: a
# container cannot see your Codex logs unless you mount them, for example
#   docker run -i --rm -v ~/.codex:/home/codexwatch/.codex:ro codexwatch
# codexwatch has no dependencies, so the image runs the source as it is and pip
# fetches nothing. The base image is pinned by digest; Dependabot keeps it current.
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
WORKDIR /app
COPY LICENSE ./
COPY codexwatch ./codexwatch
RUN useradd --create-home codexwatch
USER codexwatch
ENV PYTHONPATH=/app
ENTRYPOINT ["python", "-m", "codexwatch", "mcp"]
