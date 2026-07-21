FROM apify/actor-python:3.12

ENV PLAYWRIGHT_BROWSERS_PATH=/usr/share/ms-playwright

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium --with-deps

COPY . ./

CMD ["python3", "src/main.py"]
