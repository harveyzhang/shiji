#!/bin/sh
# Deploy the committed tree to a server over SSH and (re)start the container on 127.0.0.1:8025.
# Keys live only in $REMOTE/secrets on the server (dir 700, files 600): wsa.env, toklens.env and optionally minimax.env.
# This script never copies or prints them; it stops if they are missing.
# Usage: deploy/deploy.sh <ssh-host-alias> [month-cap] [day-cap]
set -eu
HOST=${1:?ssh host alias}
MONTH_CAP=${2:-1500}
DAY_CAP=${3:-300}
REMOTE=${SHIJI_REMOTE_DIR:-/opt/shiji}
cd "$(dirname "$0")/.."
ssh "$HOST" "test -f $REMOTE/secrets/wsa.env && test -f $REMOTE/secrets/toklens.env && mkdir -p $REMOTE/data"
git archive --format=tar HEAD | ssh "$HOST" "rm -rf $REMOTE/build.new && mkdir -p $REMOTE/build.new && \
  tar -x -C $REMOTE/build.new && rm -rf $REMOTE/build && mv $REMOTE/build.new $REMOTE/build"
ssh "$HOST" "cd $REMOTE/build && docker build -q -t shiji:latest -f deploy/Dockerfile . >/dev/null && \
  (docker rm -f shiji >/dev/null 2>&1 || true) && \
  docker run -d --name shiji --restart unless-stopped --network host \
    -v $REMOTE/data:/data -v $REMOTE/secrets:/secrets:ro \
    -e SHIJI_WSA_KEY_FILE=/secrets/wsa.env -e SHIJI_LEDGER=/data/wsa_usage.json -e SHIJI_CACHE_DIR=/data/search-cache \
    -e SHIJI_MINIMAX_KEY_FILE=/secrets/minimax.env -e SHIJI_VISION_KEY_FILE=/secrets/toklens.env -e SHIJI_ARCHIVE_DIR=/data/cases -e SHIJI_ORDER_DIR=/data/orders -e SHIJI_MONTH_CAP=$MONTH_CAP -e SHIJI_DAY_CAP=$DAY_CAP \
    shiji:latest >/dev/null && \
  for i in 1 2 3 4 5 6 7 8 9 10; do curl -fs -o /dev/null http://127.0.0.1:8025/api/health && break; sleep 1; done && \
  curl -fs http://127.0.0.1:8025/api/health"
