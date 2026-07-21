FROM apify/actor-python:3.12

ENV PYTHONPATH=/usr/src/app

# Firefox/camoufox dependencies missing from base image
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgtk-3-0 \
    libdbus-glib-1-2 \
    libxt6 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m camoufox fetch

COPY . ./

CMD ["python3", "src/main.py"]
