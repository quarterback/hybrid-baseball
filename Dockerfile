FROM python:3.12-slim

WORKDIR /app

# ffmpeg lets o27audio transcode the stitched WAV to a phone-friendly MP3.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY o27/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python", "o27v2/manage.py", "runserver"]
