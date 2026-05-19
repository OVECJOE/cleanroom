<div align="center">

```
 ██████╗██╗     ███████╗ █████╗ ███╗   ██╗██████╗  ██████╗  ██████╗ ███╗   ███╗
██╔════╝██║     ██╔════╝██╔══██╗████╗  ██║██╔══██╗██╔═══██╗██╔═══██╗████╗ ████║
██║     ██║     █████╗  ███████║██╔██╗ ██║██████╔╝██║   ██║██║   ██║██╔████╔██║
██║     ██║     ██╔══╝  ██╔══██║██║╚██╗██║██╔══██╗██║   ██║██║   ██║██║╚██╔╝██║
╚██████╗███████╗███████╗██║  ██║██║ ╚████║██║  ██║╚██████╔╝╚██████╔╝██║ ╚═╝ ██║
 ╚═════╝╚══════╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝ ╚═════╝  ╚═════╝ ╚═╝     ╚═╝
```

**A disposable, virtualized Android phone that exists only as long as you need it.**  
Spin one up. Do your thing. Watch it vanish.

[![License: MIT](https://img.shields.io/badge/License-MIT-slate.svg)](LICENSE)
[![Platform: Linux/KVM](https://img.shields.io/badge/Platform-Linux%20%2F%20KVM-teal.svg)]()
[![Android: Go Edition](https://img.shields.io/badge/Android-Go%20Edition-green.svg)]()
[![Privacy: Tor%2FWireGuard](https://img.shields.io/badge/Privacy-Tor%20%2F%20WireGuard-purple.svg)]()
[![Payments: Monero](https://img.shields.io/badge/Payments-Monero%20%2F%20XMR-purple.svg)]()

[**Try it live →**](https://cleanroom.sh) &nbsp;·&nbsp; [Architecture deep-dive](docs/architecture.md) &nbsp;·&nbsp; [The full technical guide](docs/guide.md)

</div>

---

## What is CleanRoom?

You know how people rent burner phones? A stranger's SIM, a clean handset, no history, no trace — used for a few hours then handed back. People rent them to verify accounts without exposing their real number, to test apps without contaminating their real device, to browse without building a profile that follows them across the internet.

CleanRoom is that, but better. Instead of driving somewhere to pick up a physical device, you open a browser tab. Instead of trusting whoever last used that handset, you get a phone that was literally just created for you — freshly provisioned, zero history, zero previous user's data. Instead of handing it back, it self-destructs: the moment your session ends, the entire machine is deleted and the RAM it occupied is overwritten. Nothing is archived. No screenshots, no logs of what you did inside, no persistent state of any kind.

Under the hood, CleanRoom spins up a Docker container running a full Android Go instance (via [ReDroid](https://github.com/remote-android/redroid-doc)), streams its screen to your browser in real time, routes all traffic through Tor or WireGuard so your session has a unique IP with no link to you, and then destroys the container on exit with the completeness of a shredder.

Each session is genuinely isolated from every other session on the machine. One user cannot see another user's screen, cannot reach their network traffic, cannot access their files. Different network namespaces, different bridge networks, different cgroup memory pools. The kernel enforces the walls.

---

## Why this exists

The phone in your pocket is a snitch. It remembers everything — cookies that persist across incognito mode, DNS queries that tell your ISP where you went, device fingerprints that follow you even when you switch browsers, cached media that lingers on flash storage long after you think it is gone.

Some things you just do not want on your primary device. Not because you are doing anything wrong — but because privacy is not a crime, and the infrastructure of modern apps is built to know as much about you as possible, forever.

CleanRoom is the answer that does not require you to carry a second phone, buy a prepaid SIM, use a sketchy emulator on your own machine that still knows your real IP, or trust a VPN that logs your activity. It is a clean room in the literal sense: a sterile, controlled environment that you step into, do your work, and step out of — and everything you touched inside gets incinerated.

---

## What you can do with it

A fresh Android instance with a browser and a clean IP is more useful than it sounds. Here are the practical use cases, stated plainly.

**Anonymous account verification.** Many services — crypto exchanges, privacy-focused messaging apps, certain banking apps — send OTPs to a phone number. You can use a VOIP number inside the CleanRoom session to receive that OTP without linking it to your real device's fingerprint or location.

**Testing suspicious links.** Someone sent you a link that might be phishing, might install something, might be tracking your exact device details. Open it inside CleanRoom. Whatever it tries to do happens inside a container that will not exist in five minutes. Your real device never touched it.

**Competitor and market research.** Opening your real browser to check a competitor's pricing or a supplier's site teaches their analytics exactly who you are, what your company IP is, and how often you are watching them. CleanRoom sessions look like anonymous users from random exit nodes. You see their real face, not the one they would show you knowing it is you.

**App testing with a clean slate.** As a developer, you often need to test onboarding flows, first-launch experiences, or permission prompts on a device with absolutely no prior history of your app. CleanRoom gives you that with no factory reset, no secondary device, no fuss.

**Sensitive personal transactions.** Medical research, legal research, financial planning — things where you do not want the topic bleeding into your ad profile, your browser's suggestion history, or your phone's learned behaviors.

**The physical burner phone use case, minus the physics.** Every reason someone rents a burner phone translates directly here, with the added benefit that it is cheaper, instantly available, and leaves no physical paper trail.

---

## How it works

When you click "Start session," the following happens in roughly the order listed:

A session ID is generated and a dedicated isolated Docker bridge network is created for your session and only your session. A Tor or WireGuard proxy sidecar container is started on that network — this becomes the only route traffic from your Android instance can take to reach the internet. The Android container itself is started with a strict memory ceiling, a CPU share limit, its `/data` directory mounted entirely in RAM (never touching disk), and no ability to communicate with any other container on the machine.

Android boots — this takes 20 to 30 seconds on a warm host. Once the ADB interface is ready, a lightweight screen capture server is pushed into the container and begins encoding the display as H.264 video. That video stream is proxied through the CleanRoom backend to your browser via WebSocket. Your touch input travels the reverse path — from browser to backend to ADB input commands inside the container.

You use the phone. Everything you do happens inside that container: the browsing history, the cookies, the downloaded files, the app state.

When you end the session — by clicking the button, closing the tab, or when the session timer expires — the container receives a kill signal. The PID namespace collapses. The `tmpfs` RAM mount is freed. The network namespace and all its iptables rules are deleted. The container's filesystem overlay is removed. The proxy sidecar is stopped and removed. The session network is deleted.

From first keystroke to complete erasure: everything that happened in your session is gone.

---

## Repo structure

```
cleanroom/
├── infra/
│   ├── setup.sh              # one-shot VPS provisioning script
│   ├── kernel-modules.conf   # binder + ashmem autoload config
│   ├── zram.service          # systemd unit for zRAM configuration
│   ├── apparmor/             # AppArmor profiles for container hardening
│   └── sysctl.conf           # kernel tuning parameters
│
├── backend/
│   ├── main.py               # FastAPI application entry point
│   ├── sessions.py           # container lifecycle management
│   ├── stream.py             # ADB + scrcpy WebSocket proxy
│   ├── network.py            # per-session Docker network management
│   ├── watchdog.py           # TTL enforcement and cleanup
│   └── requirements.txt
│
├── frontend/
│   ├── index.html            # landing page
│   ├── session.html          # active session view (stream + controls)
│   └── assets/
│
├── docker/
│   ├── proxy-sidecar/        # Tor/WireGuard sidecar image
│   │   └── Dockerfile
│   └── compose.dev.yml       # local development stack
│
└── docs/
    ├── architecture.md       # system design reference
    ├── guide.md              # full technical education document
    ├── security.md           # threat model and mitigations
    └── api.md                # REST + WebSocket API reference
```

---

## Running locally

Local development runs the full stack minus the Tor routing — traffic from the Android container goes directly through the host. You get the complete Android-in-Docker experience without needing a VPS.

**Prerequisites.** You need a Linux machine or VM with KVM support. This will not run on macOS natively, will not run on WSL2, and will not run on an OpenVZ or LXC-based VPS. You need Docker (installed from the official Docker repository, not snap), Python 3.11+, and ADB tools.

Verify KVM is accessible:

```bash
ls /dev/kvm
# should exist. if it does not, enable virtualization in your BIOS.
```

**Clone and enter the repo:**

```bash
git clone https://github.com/your-org/cleanroom.git
cd cleanroom
```

**Load the Android kernel modules.** These are required for Android to boot inside Docker:

```bash
sudo modprobe binder_linux devices="binder,hwbinder,vndbinder"
sudo modprobe ashmem_linux
```

To make these survive reboots, copy the provided config:

```bash
sudo cp infra/kernel-modules.conf /etc/modules-load.d/cleanroom.conf
```

**Pull the ReDroid Android image:**

```bash
docker pull redroid/redroid:12.0.0-latest
```

**Install Python dependencies:**

```bash
cd backend
pip install -r requirements.txt
```

**Start the development stack:**

```bash
docker compose -f docker/compose.dev.yml up -d
uvicorn backend.main:app --reload --port 8000
```

Open `http://localhost:8000` in your browser. Click "Start session." Wait about 30 seconds for Android to boot. You should see the Android home screen streaming in your browser tab.

Touch input, keyboard input, and the destroy-on-close logic all work exactly as they do in production. The only difference is that sessions on local dev use the host's real internet connection rather than Tor.

---

## Deploying to a VPS

The `infra/setup.sh` script handles the full provisioning of a fresh Ubuntu 22.04 VPS. It installs Docker, configures zRAM, loads the kernel modules persistently, applies the sysctl tuning parameters, sets up nginx as a reverse proxy with automatic TLS via Let's Encrypt, creates a non-root service user, and starts the CleanRoom backend as a systemd service.

**Minimum specs.** 4 GB RAM, 2 vCPU, 40 GB SSD, Ubuntu 22.04 LTS, KVM virtualization. Hetzner's CX22 (€4.49/month at time of writing) meets every requirement. DigitalOcean's Basic 4GB droplet and Vultr's Regular Performance 4GB also work. Do not use an OpenVZ-based provider — the kernel module requirement rules them out.

**Run the setup script:**

```bash
ssh root@YOUR_VPS_IP
curl -fsSL https://raw.githubusercontent.com/your-org/cleanroom/main/infra/setup.sh | bash -s -- --domain your-domain.com
```

The script will prompt for your domain name (ensure the DNS A record points to the VPS IP before running), then handle everything else. Total provisioning time is around 3 to 5 minutes. When it finishes, visiting your domain should show the CleanRoom interface.

For a manual step-by-step setup, see [docs/architecture.md](docs/architecture.md).

---

## Capacity and limits

On a 4 GB VPS, CleanRoom enforces a hard limit of 3 concurrent sessions by default. This leaves comfortable headroom for the host OS, the backend services, and variance in per-session memory usage. A 4th session request is queued and started the moment an existing session ends.

Each session is given 512 MB of memory and 1 vCPU. Sessions have a hard TTL — after which they are destroyed automatically regardless of activity. The timer is visible to the user. Default sessions last 10 minutes; longer durations are available through on-chain payment.

If you need more capacity, run multiple VPS nodes and point them at a shared session coordinator. The backend is stateless enough to support this with a Redis-backed session store. See [docs/architecture.md](docs/architecture.md) for the multi-node setup.

---

## Privacy guarantees and honest limits

CleanRoom makes specific, verifiable guarantees and some things it deliberately does not promise.

What is guaranteed: your session data never touches persistent storage. The Android `/data` partition is RAM-only and freed on session end. No session logs what you did inside the Android browser. Network traffic exits through Tor or WireGuard, so the sites you visit see a Tor exit node or VPN server IP, not your real IP or the VPS's IP. Sessions are network-isolated from each other at the kernel namespace level.

What is not guaranteed: CleanRoom cannot protect you from a compromised client device. If the machine or phone you are using to access the session has malware, that malware may capture what appears on your screen. CleanRoom also cannot prevent the service operator (whoever is hosting the VPS) from inspecting a running container in real time — the `--privileged` Docker flag required by ReDroid means host root access implies container access. Self-hosting solves this if operator trust is a concern.

The complete threat model is documented in [docs/security.md](docs/security.md).

---

## Payments & Access

CleanRoom uses on-chain payments exclusively. No email, no account, no KYC. Your wallet is your identity.

**Currency:** Monero (XMR). Ring signatures, stealth addresses, confidential transactions. No one can see who sent what to whom.

**How it works:**

1. **Connect your wallet.** Cake Wallet, Monerujo, or any Monero wallet. There is no registration screen. No email field. Nothing to fill out.
2. **Pick a duration.** 30 minutes, 1 hour, 3 hours — each with a fixed XMR price. The price covers compute (CPU + RAM allocated to your container), bandwidth (Tor exit node infrastructure), and the disposable guarantee.
3. **Send payment.** You get a unique stealth address for your session request. Send the exact XMR amount. The stealth address prevents linking your transaction to any other session or to your wallet's public identity.
4. **On-chain verification.** The backend monitors the chain. After one confirmation, it mints a short-lived JWT tied to that transaction's payment ID. This token grants access to exactly one session for the duration you paid for.
5. **Use and vanish.** Your session runs for the paid duration. When the TTL expires, the watchdog hard-kills the container. The tmpfs `/data` is freed. All network namespaces, iptables rules, and filesystem overlays are deleted. No logs of what you did inside are kept.

**Free tier.** A limited free session (10 minutes, Tor routing only) is available with no payment required. One concurrent free session at a time.

**Why not a smart contract.** Monero does not support smart contracts, and that is exactly the point. There is no on-chain escrow, no refund automation, no programmable logic. You trust the operator to deliver after payment — but the payment itself is untraceable, and the session data is ephemeral. The tradeoff is deliberate: you sacrifice trustless refunds in exchange for genuine financial privacy. If operator trust is a concern, self-host.

---

## Self-hosting

A hosted version of CleanRoom is available at **[cleanroom.sh](https://cleanroom.sh)** with the Monero payment flow described above.

If operator trust is a concern, you can self-host the entire stack on your own VPS. The `infra/setup.sh` script handles full provisioning. See the [Deploying to a VPS](#deploying-to-a-vps) section above. Self-hosting gives you complete control: no payment gateway, no shared infrastructure, no one else who can inspect a running container.

---

## Contributing

CleanRoom is open source and welcomes contributions. The areas most in need of work are GPU passthrough support for smoother Android rendering, the Monero payment monitor integration, a multi-node session coordinator for horizontal scaling, a more polished frontend session UI, and additional proxy routing options beyond Tor and WireGuard.

Open an issue before starting significant work so we can discuss the approach. For bug reports, include the output of `docker logs [container-id]` and the browser console log — most issues are diagnosable from those two sources.

---

## License

MIT. See [LICENSE](LICENSE).

---

<div align="center">
<sub>Built for people who understand that privacy is not something you apologize for wanting.</sub>
</div>
