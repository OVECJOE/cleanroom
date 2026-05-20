#!/bin/sh

# Tor needs a moment to build its initial circuits before we accept connections.
tor -f /etc/tor/torrc &
TOR_PID=$!

# Wait for Tor to be ready
echo "Waiting for Tor to build circuits..."
for i in $(seq 1 30); do
    nc -z 127.0.0.1 9050 2>/dev/null && echo "Tor ready" && break
    sleep 1
done

# Start Tinyproxy in foreground
tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf &
PROXY_PID=$!

# Wait for either process to exit
wait $TOR_PID $PROXY_PID