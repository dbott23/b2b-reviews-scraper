FROM apify/actor-python:3.11

COPY requirements.txt ./
RUN pip install -r requirements.txt
RUN playwright install chromium --with-deps

COPY . ./

CMD ["python", "-m", "src.main"]
