FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

# eMudhra/emSign intermediate CA incident (2026-10-20): pip trusts the
# compromised intermediates through its vendored CA bundle regardless of the
# system store, so replace it from a hash-pinned wheel before installing
# anything. Needs pip >= 26.2.2.
RUN python -m pip install --no-cache-dir --no-deps \
    "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"

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
