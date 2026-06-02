# PatchKit

A lightweight home server patch manager. SSH into your Linux hosts, check for pending package updates, apply upgrades, and track reboot requirements from a single web UI.

## Features

- **Dashboard** - at-a-glance view of all hosts, pending updates, security flags, and reboot status
- **Hosts** - add, edit, scan, and patch individual servers over SSH
- **Credentials** - store SSH keys and sudo config in the database; assign to hosts by name
- **Groups** - tag-based host groups with bulk scan, patch, and rolling reboot
- **Updates** - per-host pending package list with live search filter
- **Schedules** - cron-based automated patching with per-schedule host selection
- **History** - full patch run logs with per-run output
- **Notifications** - email (SMTP) and webhook (Telegram, Slack, Discord, ntfy, etc.)
- **Sudo elevation** - connect as a non-root user; PatchKit wraps privileged commands with sudo automatically (NOPASSWD or password)
- **Mobile** - responsive layout with collapsing sidebar
- **Supports** apt (Debian, Ubuntu, Raspberry Pi OS) and dnf/rpm (Fedora, Rocky Linux, RHEL, AlmaLinux, CentOS, Nobara)
- **Forward auth** - optional reverse proxy authentication (Authentik, Authelia, etc.)
- **Auto-refresh** - dashboard silently updates every 30 seconds

## Docker

```bash
docker volume create patchkit-data

docker run -d \
  --name patchkit \
  --restart unless-stopped \
  -p 8080:8080 \
  -v patchkit-data:/app/data \
  ghcr.io/msmcpeake/patchkit:latest
```

Or with Docker Compose:

```bash
curl -O https://raw.githubusercontent.com/msmcpeake/patchkit/main/docker-compose.yml
docker compose up -d
```

## Requirements

- Python 3.11+
- SSH key access to your hosts (ed25519 recommended)
- Linux host to run PatchKit on

## Install

```bash
git clone https://github.com/msmcpeake/patchkit.git
cd patchkit

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

uvicorn app:app --host 0.0.0.0 --port 8080
```

Open `http://your-server:8080`.

## Run as a systemd service

```bash
# Create a dedicated low-privilege user
useradd --system --home-dir /opt/patchkit --no-create-home --shell /usr/sbin/nologin patchkit
chown -R patchkit:patchkit /opt/patchkit

cp patchkit.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now patchkit
```

Edit `WorkingDirectory` and `ExecStart` in `patchkit.service` if you installed somewhere other than `/opt/patchkit`.

## SSH credentials

The recommended approach is to use **Credential sets** (Credentials page in the UI). Paste your private key content directly into the database; no file path management needed. Assign a credential to one or many hosts.

If you prefer path-based keys, set the key path per-host or globally in **Settings -> SSH defaults**. Paths are relative to the user running the service (e.g. the `patchkit` system user's home directory).

```bash
# Generate a dedicated key
ssh-keygen -t ed25519 -f ~/.ssh/patchkit_id -C "patchkit"

# Copy to each host
ssh-copy-id -i ~/.ssh/patchkit_id.pub user@192.168.1.x
```

## Sudo elevation

If your hosts do not allow direct root SSH, set the SSH user to a non-root account. PatchKit detects non-root users and automatically wraps privileged commands with `sudo`. Configure the sudo password (or leave blank for NOPASSWD) on the credential set or per-host in the host edit modal, with a global fallback in **Settings -> SSH defaults**.

Typical sudoers line for NOPASSWD:
```
youruser ALL=(ALL) NOPASSWD: ALL
```

## Webhook notifications

Configure in **Settings -> Webhook**. After every patch run PatchKit POSTs a JSON payload.

Available placeholders: `{host}` `{result}` `{result_upper}` `{packages}` `{duration}`

**Telegram example:**
```
URL:      https://api.telegram.org/bot<TOKEN>/sendMessage
Template: {"chat_id":"<CHAT_ID>","text":"PatchKit: {host} - {result_upper}\n{packages} packages in {duration}s"}
```

ntfy, Gotify, Slack, and Discord all work the same way.

## Forward auth

Set a header name in **Settings -> Access control** (e.g. `X-Authentik-Username`). PatchKit trusts the value of that header as the logged-in user identity. Any reverse proxy that injects a trusted header after authentication works (Authentik, Authelia, Caddy, nginx auth_request, etc.).

Configure your proxy before enabling this setting. To recover from a lockout: stop PatchKit, open `patchkit.db` with any SQLite client, and clear the `auth_header` value in the `settings` table.

## Rolling reboot

Groups support a rolling reboot that reboots hosts one at a time. PatchKit waits for SSH to go down, waits for it to come back, holds a configurable grace period, then rescans to clear the reboot-required flag before moving to the next host. Useful for Kubernetes nodes where you need to maintain cluster quorum.

## Stack

- **Backend**: FastAPI, Paramiko, APScheduler
- **Frontend**: Single-page vanilla JS (no build step, no framework)
- **Database**: SQLite
- **Process**: uvicorn

## License

MIT
