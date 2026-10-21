# python:3.11, not a newer tag: pydantic-core==2.23.4 has no prebuilt wheel
# for Python 3.14 and fails to compile in a minimal container (same issue
# already hit and fixed on Render — see render.yaml's PYTHON_VERSION pin).
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# No --workers flag: embedded ChromaDB (PersistentClient) is only safe with a
# single worker process — see vectorstore.py's module docstring.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
