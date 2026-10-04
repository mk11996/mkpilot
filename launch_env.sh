#!/usr/bin/bash

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1

if [ -z "$REQUIRED_NEOS_VERSION" ]; then
  export REQUIRED_NEOS_VERSION="19.1"
fi

if [ -z "$AGNOS_VERSION" ]; then
  export AGNOS_VERSION="4"
fi

if [ -z "$PASSIVE" ]; then
  export PASSIVE="1"
fi

export STAGING_ROOT="/data/safe_staging"

# 自建 openpilot 服务器。API_HOST/ATHENA_HOST 未单独设置时会由此地址自动推导。
# export OPENPILOT_SERVER_URL="http://192.168.5.3:18080"
export OPENPILOT_SERVER_URL="https://www.39love.net"
