# YouTube link -> piano MIDI for the EZ-310 tutor (ByteDance piano transcription model, CPU).
FROM python:3.11-slim

RUN apt-get update \
	&& apt-get install -y --no-install-recommends ffmpeg curl unzip ca-certificates \
	&& rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JavaScript runtime for YouTube
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
	&& pip install --no-cache-dir piano_transcription_inference mido "yt-dlp[default]"

# Fast but rough engine (Spotify basic-pitch, TFLite): ~1x song length on a Pi 4 instead of ~20x,
# about 60% note agreement with the ByteDance model on a test song
RUN pip install --no-cache-dir basic-pitch

WORKDIR /app
COPY transcribe.py ./
ENTRYPOINT ["python", "/app/transcribe.py"]
