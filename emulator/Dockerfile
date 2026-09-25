FROM python:3.12-slim
WORKDIR /app
COPY emulator/stream.py /app/stream.py
COPY data /app/data
ENV PYTHONUNBUFFERED=1
EXPOSE 9000
CMD ["python", "stream.py"]
