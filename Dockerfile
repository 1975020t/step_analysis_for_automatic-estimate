# API (FastAPI) and the Streamlit demo from one image; docker-compose.yml starts both.
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
COPY app.py ./
COPY data ./data
COPY fonts ./fonts

# The API and the demo write output/ and data/past_quotes/ (bind-mounted from the host by docker-compose.yml).
# To run as a fixed user, set `user:` in docker-compose.yml and give that user write access to both folders.
RUN mkdir -p /app/output

EXPOSE 8000 8501
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
