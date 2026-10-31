FROM python:3.12-slim
WORKDIR /emu
COPY . /emu
ENV PYTHONUNBUFFERED=1 LOG_DIR=/logs
EXPOSE 7700 7701
CMD ["python3", "emulator.py"]
