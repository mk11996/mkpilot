FROM qa5ekf6duk4yz2es0q.xuanyuan.run/library/python:3.12-slim

ARG APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian
ARG SECURITY_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian-security
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
         sed -i "s|https\?://deb.debian.org/debian|${APT_MIRROR}|g; s|https\?://security.debian.org/debian-security|${SECURITY_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
       fi \
    && if [ -f /etc/apt/sources.list ]; then \
         sed -i "s|https\?://deb.debian.org/debian|${APT_MIRROR}|g; s|https\?://security.debian.org/debian-security|${SECURITY_MIRROR}|g" /etc/apt/sources.list; \
       fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" -r requirements.txt
COPY app ./app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
