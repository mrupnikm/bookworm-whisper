FROM python:3.11-slim-trixie

ENV PYTHONUNBUFFERED=1
ARG ETA_TAG=v0.8.5

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Clone epub_to_audiobook repo
RUN git clone --branch "${ETA_TAG}" --single-branch --depth 1 https://github.com/p0n1/epub_to_audiobook.git /app/epub_to_audiobook

# Install epub_to_audiobook requirements
RUN pip install --no-cache-dir -r /app/epub_to_audiobook/requirements.txt

# Copy main app requirements and install
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py config.py metadata.py process.py ./
COPY routes/ ./routes/
COPY services/ ./services/
COPY templates/ ./templates/
COPY static/ ./static/
COPY tools/ ./examples/

# Create directories for books and output
RUN mkdir -p /app/books /app/output /app/logs

ENV OPENAI_API_KEY=fake
ENV OPENAI_BASE_URL=http://localhost:8880/v1

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "app:app"]
