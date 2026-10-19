ARG PYTHON_IMAGE=python:3.11-slim
FROM ${PYTHON_IMAGE}
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "gov_mcp.server", "--host", "0.0.0.0", "--port", "8765", "--runtime", "/runtime"]
