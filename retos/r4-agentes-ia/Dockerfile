# EXTRAS vacío = solo el servidor MCP; EXTRAS="[serve]" = copiloto completo (LangGraph, RAG, UI).
FROM python:3.12-slim
ARG EXTRAS=""
WORKDIR /app
COPY pyproject.toml ./
COPY mcp_server ./mcp_server
COPY copiloto ./copiloto
RUN pip install --no-cache-dir ".${EXTRAS}"
COPY . .
ENV PYTHONUNBUFFERED=1
