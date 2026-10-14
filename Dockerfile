FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

# Copy project files so uv can resolve dependencies from pyproject.toml.
COPY pyproject.toml uv.lock ./
COPY agent/ ./agent/

# Install dependencies into the system Python so `python -m agent.app`
# can import them without activating a virtual environment.
RUN pip install --no-cache-dir uv && uv pip install --system .

# AgentCore Runtime expects the server on port 8080.
# BedrockAgentCoreApp.run() defaults to port 8080, host 0.0.0.0 in containers.
EXPOSE 8080

CMD ["python", "-m", "agent.app"]
