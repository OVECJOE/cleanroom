#!/usr/bin/env bash
# CleanRoom VPS provisioning script
# Usage: curl -fsSL https://raw.githubusercontent.com/.../setup.sh | bash -s -- --domain your-domain.com
# Run as root on a fresh Ubuntu 22.04 LTS VPS

set -euo pipefail

DOMAIN="${DOMAIN:-}"
APP_USER="cleanroom"
APP_DIR="/opt/cleanroom"
DATA_DIR="/var/lib/cleanroom"

log() { echo "[$(date '+%H:%M:%S')] $*"; }
die() { log "ERROR: $*" >&2; exit 1; }

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --domain) DOMAIN="$2"; shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

[[ $EUID -eq 0 ]] die "Must run as root"
[[ -n "$DOMAIN" ]] die "--domain is required"

log "Setting up CleanRoom on $(hostname) for domain $DOMAIN"

# 1. System update
log "Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq

# 2. Install dependencies
log "Installing dependencies..."
apt-get install -y -qq \
    curl git python3.11 python3.11-venv python3-pip \
    nginx certbox python3-certbot-nginx \
    android-tools-adb \
    linux-modules-extra-$(uname -r)

# 3. Install Docker
log "Installing Docker..."
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

# 4. Load Android kernel modules
log "Configuring Android kernel modules..."
cat > /etc/modules-load.d/cleanroom.conf << 'EOF'
binder_linux
ashmem_linux
EOF


# Load them now (they will also load on next boot)
mobprobe binder_linux devices="binder,hwbinder,vndbinder" || \
    die "Could not load binder_linux. Is this a KVM VPS?"
mobprobe ashmem_linux || \
    die "Could not load ashmem_linux"

# Verify the devices exist
[[ -e /dev/binder ]] || die "/dev/binder not created by binder_linux module"
[[ -e /dev/ashmem ]] || die "/dev/ashmem not created by ashmem_linux module"
log "Kernel modules loaded and verified"

# 5. Configure zRAM
log "Setting up zRAM..."
cat > /etc/systemd/system/zram.service << 'EOF'
[Unit]
Description=Configure zRAM setup
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/sbin/mobprobe zram
ExecStart=/bin/sh -c 'echo lz4 > /sys/block/zram0/comp_algorithm'
ExecStart=/bin/sh -c 'echo 2G > /sys/block/zram0/disksize'
ExecStart=/sbin/mkswap /dev/zram0
ExecStart=/sbin/swapon -p 100 /dev/zram0
RemainAfterExist=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now zram.service
log "zRAM configured and active"

# 6. Kernel tuning
log "Applying kernel parameters..."
cat > /etc/sysctl.d/99-cleanroom.conf << 'EOF'
# Aggressive swap to zRAM to keep active pages in fast RAM
vm.swappiness=80

# Keep filesystem caches longer so that shared Android image layers benefit
vm.vfs_cache_pressure=50

# Maximum shared memory for Ashmem
kernel.shmmax=536870912 # 512MB

# Higher inotify limits for multiple containers
fs.inotify.max_user_instances=1024
fs.inotify.max_user_watches=524288

# Network performance
net.core.rmem_max=134217728
net.core.wmem_max=134217728
EOF

sysctl -p /etc/sysctl.d/99-cleanroom.conf

# 7. Create application user (non-root, in docker group)
log "Creating application user..."
useradd -r -m -s /bin/bash -G docker "$APP_USER" || \
    usermod -aG docker "$APP_USER" # user already exists.

# 8. Set up application directory
log "Setting up application directory..."
mkdir -p "$APP_DIR" "$DATA_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR" "$DATA_DIR"

# 9. Pull the Android Docker image
log "Pulling Android Docker image (this may take a few minutes)"
docker pull redroid/redroid:12.0.0-latest

# 10. Configure nginx
log "Configuring nginx..."
cat > /etc/nginx/sites-available/cleanroom << EOF
upstream cleanroom_backend {
    server 127.0.0.1:8000;
    keepalive 64;
}

server {
    listen 80;
    server_name $DOMAIN;
    # Redirect all HTTP to HTTPS
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name $DOMAIN;

    # TLS (certbot will fill these in)
    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
    ssl_prefer_server_ciphers off;

    # Security headers
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header Referrer-Policy no-referrer;

    # API proxy
    location /api/ {
        proxy_pass http://cleanroom_backend;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # WebSocket proxy for streaming
    location /stream/ {
        proxy_pass http://cleanroom_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        # WebSocket sessions can be long-lived
        proxy_read_timeout 1800s;
        proxy_send_timeout 1800s;
    }

    # Health check
    location /health {
        proxy_pass http://cleanroom_backend;
    }
}
EOF

ln -sf /etc/nginx/sites-available/cleanroom /etc/nginx/sites-enabled/cleanroom
rm -f /etc/nginx/sites-enabled/default

nginx -t || die "nginx config is invalid"

# 11. Obtain TLS certificate
log "Obtaining TLS certificate..."
certbot certonly --nginx -d "$DOMAIN" --non-interactive --agree-tos \
    --email "admin@$DOMAIN" --redirect

# 12. Create systemd service
log "Creating systemd service..."
cat > /stc/systemd/system/cleanroom.service << EOF
[Unit]
Description=CleanRoom Backend
After=network.target docker.service
Requires=docker.service

[Service]
Type=exec
User=$APP_USER
WorkingDirectory=$APP_DIR
# Environment variables
EnvironmentFile=/etc/cleanroom/env
ExecStart=$APP_DIR/.venv/bin/uvicorn cleanroom.main:app \\
    --host 127.0.0.1 \\
    --port 8000 \\
    --workers 1 \\
    --log-level info
Restart=on-failure
RestartSec=5s
TimeoutStopSec=30

# Security hardening for the systemd service itself
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ReadWritePaths=$DATA_DIR $APP_DIR

[Install]
WantedBy=multi-user.target
EOF

# 13. Create environment file
mkdir -p /etc/cleanroom
cat > /etc/cleanroom/env << EOF
CLEANROOM_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
CLEANROOM_MAX_SESSIONS=3
CLEANROOM_SESSION_MEMORY_MB=512
CLEANROOM_SESSION_TTL_SECONDS=1800
CLEANROOM_ENABLE_TOR=true
CLEANROOM_REGISTRY_PATH=$DATA_DIR/sessions.json
CLEANROOM_LOG_LEVEL=INFO
EOF

chmod 600 /etc/cleanroom/env

log "
Setup complete!

Next steps:
1. Copy the application code to $APP_DIR
2. cd $APP_DIR && python3.11 -m venv .venv && .venv/bin/pip install -e .
3. systemctl daemon-reload && systemctl enable --now cleanroom nginx
4. Check: systemctl status cleanroom
5. Check: curl https://$DOMAIN/health

The secret key has been generated in /etc/cleanroom/env
"