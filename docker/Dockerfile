# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS web-build
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
ENV VITE_DEMO_MODE=true VITE_ROBOT_WS_URL=/control VITE_VISION_PREVIEW_URL=/vision/frame.jpg
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src/massage_controller
RUN apt-get update && apt-get install -y --no-install-recommends nginx ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin anmo
WORKDIR /app
COPY src/massage_controller/requirements.txt /tmp/control-requirements.txt
# SIM uses the protocol/controller; real route math, SDK, camera and ROS are
# intentionally absent from this image. It cannot be switched into real mode.
RUN pip install --no-cache-dir -r /tmp/control-requirements.txt
COPY src/massage_controller/massage_controller/ /app/src/massage_controller/massage_controller/
COPY src/massage_controller/package.xml /app/src/massage_controller/package.xml
COPY config/rehab_protocol/ /app/config/rehab_protocol/
COPY --from=web-build /build/web/dist/ /app/web/dist/
COPY docker/nginx.conf /etc/nginx/nginx.conf
COPY docker/entrypoint.py docker/healthcheck.py docker/smoke.py /app/docker/
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "/app/docker/healthcheck.py"]
ENTRYPOINT ["python", "/app/docker/entrypoint.py"]
