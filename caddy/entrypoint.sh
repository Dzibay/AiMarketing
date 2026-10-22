#!/bin/sh
set -eu

raw="${PUBLIC_BASE_URL:-}"
host=$(printf '%s\n' "$raw" | sed -E 's#^[a-zA-Z][a-zA-Z0-9+.-]*://##; s#/.*##; s#:[0-9]+$##')
if [ -z "$host" ]; then
    echo "PUBLIC_BASE_URL is required for Caddy" >&2
    exit 1
fi

mkdir -p /etc/caddy
cat > /etc/caddy/Caddyfile <<EOF
${host} {
    encode gzip
    reverse_proxy nginx:80
}
EOF

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
