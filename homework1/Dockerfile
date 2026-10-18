FROM python:3.12-slim

WORKDIR /app

COPY app.py ./app.py

RUN useradd --create-home appuser
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=3s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"

CMD ["python", "app.py"]
