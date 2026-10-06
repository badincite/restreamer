FROM python:3.12-slim-bookworm
WORKDIR /app
RUN pip install --no-cache-dir aiohttp==3.14.4
COPY manager.py test_manager.py ./
ENV PYTHONUNBUFFERED=1
EXPOSE 8090
CMD ["python", "manager.py"]
