FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY pyproject.toml .
COPY config.yaml .
RUN pip install --no-cache-dir .

# Without this, the container defaults to UTC — every "today"/"this month"
# boundary in energy_ledger.py comes from datetime.now().astimezone(), so
# the day rolled over at 7pm/6pm local (UTC midnight) instead of real
# midnight, zeroing out savings hours before the actual day was over.
ENV TZ="America/Chicago"
ENV EG4_BATTERY_IP="192.168.130.139"
ENV EG4_BATTERY_PORT="4196"
ENV EG4_DEVICE_ID="1"
ENV EG4_MQTT_BROKER="localhost"
ENV EG4_MQTT_PORT="1883"
ENV EG4_MQTT_USER=""
ENV EG4_MQTT_PASS=""
ENV EG4_POLL_INTERVAL="30"

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000')" || exit 1

CMD ["eg4-monitor", "--config", "/app/config.yaml", "--no-ui"]
