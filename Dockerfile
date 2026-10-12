# The API (FastAPI): analysis, quotes, documents and the database of the screens. The screens are built and served
# by web/Dockerfile (nginx). docker-compose.yml starts the database, the API and the screens together.
# BASE_IMAGE lets an in-house registry or a mirror supply the Python image (default: Docker Hub)
ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# OpenCASCADE (CadQuery) needs the OpenGL / X runtime libraries even without a display
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglu1-mesa libxrender1 libxext6 libsm6 libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src ./src
COPY api ./api
COPY migrations ./migrations
COPY alembic.ini ./
COPY data ./data
COPY fonts ./fonts

# output/ holds the uploaded files, documents and job state (a volume in docker-compose.yml). The database
# schema is created / migrated by the API on start (alembic upgrade head); the initial data comes from data/.
RUN mkdir -p /app/output

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
