# The MCP server in a container, for directories that build and check servers
# (Glama). Day to day, run `codexwatch mcp` on your own machine instead: a
# container cannot see your Codex logs unless you mount them, for example
#   docker run -i --rm -v ~/.codex:/home/codexwatch/.codex:ro codexwatch
# codexwatch has no dependencies, so the image runs the source as it is and pip
# fetches nothing. The base image is pinned by digest; Dependabot keeps it current.
FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30
WORKDIR /app
COPY LICENSE ./
COPY codexwatch ./codexwatch
RUN useradd --create-home codexwatch
USER codexwatch
ENV PYTHONPATH=/app
ENTRYPOINT ["python", "-m", "codexwatch", "mcp"]
