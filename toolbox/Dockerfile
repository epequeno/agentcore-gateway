FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

# Same hash-pinned pip as the agent image: the eMudhra/emSign intermediate CA
# incident makes the vendored pip CA bundle unsafe for this account.
RUN python -m pip install --no-cache-dir --no-deps \
    "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
EXPOSE 8000
CMD ["python", "app.py"]
