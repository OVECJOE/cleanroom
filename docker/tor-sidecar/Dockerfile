FROM alpine:3.19

# tinyproxy is an HTTPS proxy that wraps the SOCKS5 Tor proxy
# because Android's global proxy setting only supports HTTP CONNECT proxying.
RUN apk add --no-cache tor tinyproxy

# Tor configuration
# The key settings:
# SocksPort 0.0.0.0:9050 -- listen on all interfaces on the bridge network,
#   not just localhost. The Android container is on a different network namespace
#   so 127.0.0.1 would only be reachable inside the Tor container.
# ExitPolicy reject *:* -- this is a client-only Tor node. We do not relay
#   traffic for others. This is important: running a relay from a VPS you do
#   not own can violate terms of service and attract scrutiny.
# IsolateDestAddr -- use a different Tor circuit for each destination address.
#   This prevents correlation between requests to different sites in the same
#   session. A session browsing both site A and site B will use different exit
#   nodes for each, making it harder to link the two.
COPY tor/torrc /etc/tor/torrc

# Tinyproxy configuration
# ListenAddress 0.0.0.0 -- accept connections from the Android container
# upstream socks5 127.0.0.1:9050 -- forward to Tor's SOCKS5 port
COPY tor/tinyproxy.conf /etc/tinyproxy/tinyproxy.conf

COPY tor/start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 8888

CMD ["/start.sh"]
