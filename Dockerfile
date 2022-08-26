# syntax=docker/dockerfile:1

FROM python:3.10-bullseye

WORKDIR /app

ENV DISPLAY :99
# ENV DEBIAN_FRONTEND=noninteractive
# ENV LIBGL_ALWAYS_INDIRECT=1

RUN apt-get update && apt-get install -y libgl1 python3-pyqt5 xvfb

COPY requirements.txt requirements.txt

RUN pip3 install -r requirements.txt

COPY . .

RUN cp config.example.py config.py

CMD [ "xvfb-run", "python3", "main.py"]