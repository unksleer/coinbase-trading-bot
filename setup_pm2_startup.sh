#!/usr/bin/env bash
set -e

echo "Configuring PM2 startup service with systemd..."
env PATH="$PATH:/home/leeunks/.nvm/versions/node/v20.20.2/bin" /home/leeunks/.nvm/versions/node/v20.20.2/lib/node_modules/pm2/bin/pm2 startup systemd -u leeunks --hp /home/leeunks
echo "PM2 startup service successfully configured!"
