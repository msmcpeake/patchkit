"""
PatchKit - Home server patch manager
Run: uvicorn app:app --host 0.0.0.0 --port 8080 --reload
"""

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import shlex
import smtplib
import socket
import sqlite3
import threading
import urllib.request
import time
from contextlib import asynccontextmanager
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import paramiko
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DATA_DIR = Path(os.environ.get("DATA_DIR", "."))
DB_PATH = DATA_DIR / "patchkit.db"
LOCK_DIR = Path("/tmp")
KNOWN_HOSTS = DATA_DIR / "patchkit_known_hosts"
_KNOWN_HOSTS_LOCK = threading.Lock()

APP_VERSION = "1.8.9"

CHANGELOG = [
    {
        "version": "1.8.9",
        "date": "2026-06-16",
        "changes": [
            "Scheduler now starts before schedules are registered, fixing stale Next Run timestamps after a restart",
        ],
    },
    {
        "version": "1.8.8",
        "date": "2026-06-16",
        "changes": [
            "Scheduled patch and autoscan jobs no longer silently skip on minor delays: misfire_grace_time raised from APScheduler's 1-second default to 1 hour",
        ],
    },
    {
        "version": "1.8.7",
        "date": "2026-06-16",
        "changes": [
            "Autoscan interval setting now actually schedules periodic scans (was previously saved but unused)",
        ],
    },
    {
        "version": "1.8.6",
        "date": "2026-06-08",
        "changes": [
            "Hosts and history tables are now sortable by clicking column headers",
            "GitHub link added to sidebar footer",
        ],
    },
    {
        "version": "1.8.5",
        "date": "2026-06-05",
        "changes": [
            "Patch and reboot monitoring redesigned as a single split-pane panel: host list on the left with status dots and last log line, full log on the right for the selected host; replaces the stacked card layout that cluttered the screen when patching many hosts at once",
        ],
    },
    {
        "version": "1.8.4",
        "date": "2026-06-05",
        "changes": [
            "Patch/reboot cards auto-collapse their log output 3 seconds after completing, reducing clutter when patching many hosts at once; click the header to re-expand",
        ],
    },
    {
        "version": "1.8.3",
        "date": "2026-06-04",
        "changes": [
            "Schedules now support groups/tags as targets: select tags and all hosts in those groups are patched at run time",
            "Schedule list redesigned as responsive cards that display correctly on mobile",
            "Schedule modal shows a dynamic target summary: warns in amber when nothing is selected (all hosts fallback), otherwise describes exactly which groups and hosts will be patched",
        ],
    },
    {
        "version": "1.8.2",
        "date": "2026-06-03",
        "changes": [
            "Last patched column on hosts page showing days since last successful patch run",
            "Disable/enable host toggle: pause a host from scans and patches without deleting it; disabled hosts are dimmed with a grey dot",
        ],
    },
    {
        "version": "1.8.1",
        "date": "2026-06-03",
        "changes": [
            "Reboot recovery timeout is now configurable in Settings (default 300s)",
        ],
    },
    {
        "version": "1.8.0",
        "date": "2026-06-03",
        "changes": [
            "Single host reboot and reboot all now open a live monitoring card showing offline/recovery/rescan progress, same as rolling reboot",
        ],
    },
    {
        "version": "1.7.9",
        "date": "2026-06-03",
        "changes": [
            "Fix: patch output cards no longer go off-screen when patching multiple hosts; container is now scrollable and capped to viewport height",
        ],
    },
    {
        "version": "1.7.8",
        "date": "2026-06-02",
        "changes": [
            "Fix: theme toggle now correctly applies dark/light mode",
        ],
    },
    {
        "version": "1.7.7",
        "date": "2026-06-02",
        "changes": [
            "Test credential button: verify SSH connectivity to all assigned hosts from the credentials page",
            "Exclude package: ban button on each package in the updates list adds it to the host's excluded list",
            "OS badge shown on each host card in the updates page",
            "Dark/light/auto theme toggle in the sidebar",
            "Keyboard shortcuts: S to scan all, P to patch all, Escape to close modals",
            "Patch all now shows a confirmation with host and package counts before firing",
        ],
    },
    {
        "version": "1.7.6",
        "date": "2026-06-02",
        "changes": [
            "Fix: log modal no longer opens behind the host history modal",
        ],
    },
    {
        "version": "1.7.5",
        "date": "2026-06-02",
        "changes": [
            "Patch verification: re-scan after upgrade to detect packages skipped by the package manager (dependency conflicts, holds); report them as warnings instead of falsely claiming success",
        ],
    },
    {
        "version": "1.7.4",
        "date": "2026-06-02",
        "changes": [
            "Scan all button added to hosts page toolbar",
        ],
    },
    {
        "version": "1.7.3",
        "date": "2026-06-02",
        "changes": [
            "Bulk import: add multiple hosts at once from a textarea (name ip per line) with shared credential, port, and tags",
            "Clone host: duplicate a host entry pre-filled in the add modal with a -copy name suffix",
            "Ping: TCP reachability check with latency shown inline on the host row",
        ],
    },
    {
        "version": "1.7.2",
        "date": "2026-06-02",
        "changes": [
            "Phased apt updates detected on scan and shown with a PHASED X% badge in the updates list",
        ],
    },
    {
        "version": "1.7.1",
        "date": "2026-06-02",
        "changes": [
            "Docker image published to ghcr.io on each release via GitHub Actions",
        ],
    },
    {
        "version": "1.7.0",
        "date": "2026-06-02",
        "changes": [
            "Credential sets: define reusable SSH auth (user, private key, sudo password) and assign to hosts",
            "Private keys stored in the database; no more path management per host",
            "Credentials page with fingerprint display and per-credential host count",
            "Host add/edit: credential dropdown collapses manual auth fields when a set is selected",
        ],
    },
    {
        "version": "1.6.1",
        "date": "2026-06-02",
        "changes": [
            "Sudo elevation: non-root SSH users now run privileged commands via sudo automatically",
            "Per-host and global sudo password support (NOPASSWD or explicit password)",
            "Sudo wraps complex compound commands safely via sudo sh -c to avoid shell-quoting issues",
        ],
    },
    {
        "version": "1.6.0",
        "date": "2026-06-02",
        "changes": [
            "Batch reboot: reboot all hosts pending a kernel update from the dashboard alert banner",
            "Scan before patch: optional setting to run a fresh scan immediately before applying updates",
            "Host notes: free-text notes field per host, shown in the host list",
            "Updates page: live package-name filter across all hosts",
        ],
    },
    {
        "version": "1.5.3",
        "date": "2026-06-02",
        "changes": [
            "Mobile support: responsive layout with collapsing sidebar drawer, hamburger menu header bar, 2-column metrics on tablet, horizontal-scrolling tables, single-column forms, and full-width patch log cards on small screens",
        ],
    },
    {
        "version": "1.5.2",
        "date": "2026-06-01",
        "changes": [
            "Parallel patching: patch all, patch group, and patch all groups now run all hosts simultaneously instead of sequentially",
            "Scheduled patches also run all hosts in parallel via asyncio.gather",
        ],
    },
    {
        "version": "1.5.1",
        "date": "2026-06-01",
        "changes": [
            "Security: excluded_pkgs values are now shell-quoted (shlex.quote) before use in SSH commands",
            "Security: SSH connections use TOFU known-hosts verification (patchkit_known_hosts) instead of blindly accepting any host key",
            "Security: patch lock file acquisition is now atomic (O_CREAT|O_EXCL) to eliminate TOCTOU race",
            "Security: SMTP notifications attempt STARTTLS before sending",
            "Security: host update endpoint guards against unexpected column names via explicit allowlist",
            "History API: limit parameter capped at 500",
        ],
    },
    {
        "version": "1.5.0",
        "date": "2026-06-01",
        "changes": [
            "Rolling reboot for host groups: reboots one node at a time, waits for SSH recovery before proceeding",
            "Automatic rescan after reboot (normal and rolling) to clear the reboot-required flag",
            "Auto-refresh: dashboard, hosts, updates, and groups silently refresh every 30s when scan data changes",
        ],
    },
    {
        "version": "1.4.0",
        "date": "2026-05-30",
        "changes": [
            "Authentik forward auth support via configurable header (auth_header setting)",
            "Logged-in username and email displayed in sidebar",
            "Nobara Linux: patch via nobara-sync cli instead of dnf upgrade",
            "PatchKit service runs as dedicated system user, not root",
            "Versioning system and changelog",
        ],
    },
    {
        "version": "1.3.0",
        "date": "2026-05-30",
        "changes": [
            "Webhook notifications with configurable JSON body template",
            "Placeholders: {host}, {result}, {result_upper}, {packages}, {duration}",
            "Test webhook button in settings (tests without saving first)",
            "Schedule editing: edit name, cron expression, and host assignment",
        ],
    },
    {
        "version": "1.2.0",
        "date": "2026-05-29",
        "changes": [
            "Updates page: Security only filter toggle",
            "Updates page: Patch security button for hosts with flagged packages",
            "Groups: patch all groups button",
            "Dashboard: security update alert banner",
            "Hosts: OS detect button in add/edit modals",
        ],
    },
    {
        "version": "1.1.0",
        "date": "2026-05-29",
        "changes": [
            "Tag-based host groups with bulk scan and patch",
            "DNF/RPM family support: Fedora, RHEL, Rocky, Alma, CentOS, Nobara",
            "Security update detection via dnf updateinfo",
            "Reboot detection for dnf hosts (needs-restarting / kernel compare)",
            "Disk usage and uptime collected on every scan",
            "Per-host excluded packages (held during upgrade)",
            "Host enable/disable toggle",
        ],
    },
    {
        "version": "1.0.0",
        "date": "2026-05-28",
        "changes": [
            "Initial release",
            "Dashboard with host metrics and pending update counts",
            "Host management: add, edit, delete, enable/disable",
            "SSH scan: apt package list, reboot detection, disk, uptime",
            "One-click patch with live streaming log output",
            "Patch history with per-run logs",
            "Cron schedules via APScheduler",
            "Settings: SSH defaults, scan interval, SMTP email notifications",
            "Per-host SSH key, user, port, and OS override",
        ],
    },
]

# Semaphore limits concurrent SSH scans to 5
_SCAN_SEM = asyncio.Semaphore(5)

scheduler = AsyncIOScheduler()


_AUTH_HEADER: str = ""


def _reload_auth_header():
    global _AUTH_HEADER
    try:
        db = get_db()
        row = db.execute("SELECT value FROM settings WHERE key='auth_header'").fetchone()
        db.close()
        _AUTH_HEADER = (row["value"] if row else "").strip()
    except Exception:
        _AUTH_HEADER = ""


def _reload_autoscan_job():
    try:
        db = get_db()
        row = db.execute("SELECT value FROM settings WHERE key='scan_interval_hours'").fetchone()
        db.close()
        hours = int(row["value"]) if row and row["value"] else 12
    except Exception:
        hours = 12

    if scheduler.get_job("autoscan"):
        scheduler.remove_job("autoscan")
    scheduler.add_job(
        scan_all,
        IntervalTrigger(hours=hours),
        id="autoscan",
        replace_existing=True,
        misfire_grace_time=3600,
    )


@asynccontextmanager
async def lifespan(app_: FastAPI):
    _reload_auth_header()
    scheduler.start()
    await _reload_all_schedules()
    _reload_autoscan_job()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="PatchKit", lifespan=lifespan)


@app.middleware("http")
async def forward_auth_middleware(request: Request, call_next):
    if _AUTH_HEADER and not request.headers.get(_AUTH_HEADER):
        return Response(
            "Unauthorized: forward auth header missing. "
            "Ensure your reverse proxy is configured.",
            status_code=401,
            media_type="text/plain",
        )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS hosts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL UNIQUE,
            ip            TEXT NOT NULL,
            port          INTEGER DEFAULT 22,
            ssh_user      TEXT DEFAULT 'root',
            ssh_key       TEXT DEFAULT '~/.ssh/id_ed25519',
            os_name       TEXT DEFAULT 'Debian 12',
            role          TEXT DEFAULT '',
            tags          TEXT DEFAULT '',
            excluded_pkgs TEXT DEFAULT '',
            enabled       INTEGER DEFAULT 1,
            created_at    TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS scan_results (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            host_id       INTEGER NOT NULL,
            scanned_at    TEXT DEFAULT (datetime('now')),
            status        TEXT,
            pkg_count     INTEGER DEFAULT 0,
            packages      TEXT DEFAULT '[]',
            reboot_req    INTEGER DEFAULT 0,
            disk_used_pct TEXT DEFAULT '',
            disk_free     TEXT DEFAULT '',
            uptime_str    TEXT DEFAULT '',
            error         TEXT DEFAULT '',
            FOREIGN KEY (host_id) REFERENCES hosts(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS patch_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at  TEXT DEFAULT (datetime('now')),
            finished_at TEXT,
            host_ids    TEXT,
            host_names  TEXT,
            pkg_count   INTEGER DEFAULT 0,
            result      TEXT DEFAULT 'ok',
            duration_s  REAL,
            log         TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS schedules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            host_ids    TEXT DEFAULT '[]',
            cron_expr   TEXT,
            enabled     INTEGER DEFAULT 1,
            last_run    TEXT,
            next_run    TEXT
        );

        CREATE TABLE IF NOT EXISTS credentials (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            ssh_user    TEXT DEFAULT 'root',
            ssh_key     TEXT DEFAULT '',
            sudo_pass   TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        INSERT OR IGNORE INTO settings VALUES ('ssh_key',   '~/.ssh/id_ed25519');
        INSERT OR IGNORE INTO settings VALUES ('ssh_user',  'root');
        INSERT OR IGNORE INTO settings VALUES ('ssh_port',  '22');
        INSERT OR IGNORE INTO settings VALUES ('ssh_timeout','10');
        INSERT OR IGNORE INTO settings VALUES ('scan_interval_hours', '12');
        INSERT OR IGNORE INTO settings VALUES ('smtp_relay', '');
        INSERT OR IGNORE INTO settings VALUES ('notify_email', '');
        INSERT OR IGNORE INTO settings VALUES ('webhook_url', '');
        INSERT OR IGNORE INTO settings VALUES ('webhook_template', '');
        INSERT OR IGNORE INTO settings VALUES ('auth_header', '');
        INSERT OR IGNORE INTO settings VALUES ('auto_security', '0');
        INSERT OR IGNORE INTO settings VALUES ('require_reboot_confirm', '1');
        INSERT OR IGNORE INTO settings VALUES ('scan_before_patch', '0');
        INSERT OR IGNORE INTO settings VALUES ('sudo_pass', '');
        """)
        conn.commit()


def _migrate_db():
    migrations = [
        "ALTER TABLE scan_results ADD COLUMN error TEXT DEFAULT ''",
        "ALTER TABLE scan_results ADD COLUMN disk_used_pct TEXT DEFAULT ''",
        "ALTER TABLE scan_results ADD COLUMN disk_free TEXT DEFAULT ''",
        "ALTER TABLE scan_results ADD COLUMN uptime_str TEXT DEFAULT ''",
        "ALTER TABLE hosts ADD COLUMN tags TEXT DEFAULT ''",
        "ALTER TABLE hosts ADD COLUMN excluded_pkgs TEXT DEFAULT ''",
        "ALTER TABLE hosts ADD COLUMN notes TEXT DEFAULT ''",
        "ALTER TABLE hosts ADD COLUMN sudo_pass TEXT DEFAULT ''",
        "ALTER TABLE hosts ADD COLUMN credential_id INTEGER",
        "ALTER TABLE schedules ADD COLUMN tags TEXT DEFAULT ''",
    ]
    db = get_db()
    for sql in migrations:
        try:
            db.execute(sql)
            db.commit()
        except Exception:
            pass
    db.close()


init_db()
_migrate_db()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class HostCreate(BaseModel):
    name: str
    ip: str
    port: int = 22
    ssh_user: str = "root"
    ssh_key: str = "~/.ssh/id_ed25519"
    os_name: str = ""
    role: str = ""
    tags: str = ""
    excluded_pkgs: str = ""
    notes: str = ""
    sudo_pass: str = ""
    credential_id: Optional[int] = None

class TempHostSpec(BaseModel):
    ip: str
    port: int = 22
    ssh_user: str = "root"
    ssh_key: str = "~/.ssh/id_ed25519"

class HostUpdate(BaseModel):
    name: Optional[str] = None
    ip: Optional[str] = None
    port: Optional[int] = None
    ssh_user: Optional[str] = None
    ssh_key: Optional[str] = None
    os_name: Optional[str] = None
    role: Optional[str] = None
    tags: Optional[str] = None
    excluded_pkgs: Optional[str] = None
    notes: Optional[str] = None
    sudo_pass: Optional[str] = None
    credential_id: Optional[int] = None
    enabled: Optional[int] = None

class BulkHostEntry(BaseModel):
    name: str
    ip: str
    port: int = 22
    credential_id: Optional[int] = None
    ssh_user: str = "root"
    ssh_key: str = "~/.ssh/id_ed25519"
    tags: str = ""

class BulkImport(BaseModel):
    hosts: list[BulkHostEntry]

class SettingsPayload(BaseModel):
    settings: dict[str, str]

class CredentialCreate(BaseModel):
    name: str
    ssh_user: str = "root"
    ssh_key: str = ""
    sudo_pass: str = ""

class CredentialUpdate(BaseModel):
    name: Optional[str] = None
    ssh_user: Optional[str] = None
    ssh_key: Optional[str] = None
    sudo_pass: Optional[str] = None


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------

def _expand_key(path: str) -> Path:
    return Path(path).expanduser()


def _load_key(key_path: Path) -> paramiko.PKey:
    if not key_path.exists():
        raise FileNotFoundError(f"SSH key not found: {key_path}")
    last_exc = None
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return cls.from_private_key_file(str(key_path))
        except paramiko.ssh_exception.PasswordRequiredException:
            raise RuntimeError(f"SSH key {key_path} is passphrase-protected")
        except Exception as e:
            last_exc = e
    raise RuntimeError(f"Could not load SSH key {key_path}: {last_exc}")


def _load_key_content(content: str) -> paramiko.PKey:
    last_exc = None
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return cls.from_private_key(io.StringIO(content))
        except paramiko.ssh_exception.PasswordRequiredException:
            raise RuntimeError("SSH key is passphrase-protected")
        except Exception as e:
            last_exc = e
    raise RuntimeError(f"Could not load SSH key: {last_exc}")


def _key_fingerprint(content: str) -> str:
    if not (content or "").strip():
        return ""
    try:
        key = _load_key_content(content)
        digest = hashlib.sha256(key.asbytes()).digest()
        return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")
    except Exception:
        return "invalid"


def _make_ssh_client() -> paramiko.SSHClient:
    """Create an SSHClient with TOFU known-hosts verification."""
    client = paramiko.SSHClient()
    with _KNOWN_HOSTS_LOCK:
        if KNOWN_HOSTS.exists():
            client.load_host_keys(str(KNOWN_HOSTS))
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    return client


def _save_host_key(client: paramiko.SSHClient):
    with _KNOWN_HOSTS_LOCK:
        client.save_host_keys(str(KNOWN_HOSTS))


def ssh_connect(host) -> paramiko.SSHClient:
    db = get_db()
    defaults = {r["key"]: r["value"] for r in db.execute("SELECT key,value FROM settings")}
    cred_id = None
    try:
        cred_id = host["credential_id"]
    except (KeyError, IndexError):
        pass
    cred = db.execute("SELECT * FROM credentials WHERE id=?", (cred_id,)).fetchone() if cred_id else None
    db.close()

    port    = int(host["port"] or defaults.get("ssh_port", 22))
    timeout = int(defaults.get("ssh_timeout", 10))

    if cred:
        user = cred["ssh_user"] or defaults.get("ssh_user", "root")
        pkey = _load_key_content(cred["ssh_key"])
    else:
        user = host["ssh_user"] or defaults.get("ssh_user", "root")
        pkey = _load_key(_expand_key(host["ssh_key"] or defaults.get("ssh_key", "~/.ssh/id_ed25519")))

    client = _make_ssh_client()
    client.connect(
        hostname=host["ip"], port=port, username=user, pkey=pkey,
        timeout=timeout, banner_timeout=timeout, auth_timeout=timeout,
        look_for_keys=False, allow_agent=False,
    )
    _save_host_key(client)
    return client


async def ssh_connect_async(host: sqlite3.Row, max_attempts: int = 3) -> paramiko.SSHClient:
    """Connect with exponential backoff retry (1s, 2s, 4s)."""
    loop = asyncio.get_event_loop()
    last_exc: Exception = RuntimeError("No attempts made")
    for attempt in range(max_attempts):
        try:
            return await loop.run_in_executor(None, lambda: ssh_connect(host))
        except Exception as e:
            last_exc = e
            if attempt < max_attempts - 1:
                await asyncio.sleep(2 ** attempt)
    raise last_exc


def _resolve_sudo_pass(host, defaults: dict) -> Optional[str]:
    """Return the sudo password for this host, or None if sudo is not needed.

    Credential (if assigned) → per-host sudo_pass → global sudo_pass setting.
    Returns None when the effective user is root (no sudo needed).
    Returns '' for NOPASSWD sudo, or the password string when one is configured.
    """
    cred_id = None
    try:
        cred_id = host["credential_id"]
    except (KeyError, IndexError):
        pass

    if cred_id:
        db = get_db()
        cred = db.execute("SELECT * FROM credentials WHERE id=?", (cred_id,)).fetchone()
        db.close()
        if cred:
            user = (cred["ssh_user"] or defaults.get("ssh_user", "root")).strip()
            if user == "root":
                return None
            return (cred["sudo_pass"] or "").strip()

    user = (host["ssh_user"] or defaults.get("ssh_user", "root")).strip()
    if user == "root":
        return None
    try:
        host_pass = (host["sudo_pass"] or "").strip()
    except (KeyError, IndexError):
        host_pass = ""
    global_pass = defaults.get("sudo_pass", "").strip()
    return host_pass if host_pass else global_pass


def ssh_run(client: paramiko.SSHClient, cmd: str, timeout: int = 120,
            sudo_pass: Optional[str] = None) -> tuple[int, str, str]:
    """Run cmd over SSH. sudo_pass=None=no sudo, ''=NOPASSWD, 'pass'=password sudo."""
    if sudo_pass is not None:
        # Wrap in sh -c so compound commands (if/else, pipes, semicolons) work correctly.
        if sudo_pass:
            cmd = f"sudo -S -p '' sh -c {shlex.quote(cmd)}"
        else:
            cmd = f"sudo sh -c {shlex.quote(cmd)}"
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout, get_pty=False)
    if sudo_pass:
        stdin.write(f"{sudo_pass}\n")
        stdin.flush()
        stdin.channel.shutdown_write()
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def _strip_ansi(text: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]|\r', '', text)


def parse_upgradeable(raw: str) -> list[dict]:
    """Parse `apt list --upgradeable`. Repo name contains 'security' → is_security=True."""
    pkgs = []
    for line in raw.splitlines():
        m = re.match(r"^(\S+)/(\S+)\s+(\S+)\s+\S+\s+\[upgradabl?e from:\s+(\S+)\]", line)
        if m:
            pkgs.append({
                "name": m.group(1),
                "to":   m.group(3),
                "from": m.group(4),
                "is_security": "security" in m.group(2).lower(),
                "phased_pct": None,
            })
    return pkgs


def annotate_phased(pkgs: list[dict], policy_raw: str) -> None:
    """Parse `apt-cache policy` output and set phased_pct on matching packages."""
    current_pkg = None
    candidate = None
    for line in policy_raw.splitlines():
        m = re.match(r'^(\S+):$', line)
        if m:
            current_pkg = m.group(1)
            candidate = None
            continue
        if current_pkg is None:
            continue
        mc = re.match(r'^\s+Candidate:\s+(\S+)', line)
        if mc:
            candidate = mc.group(1)
            continue
        if candidate:
            mv = re.match(r'^\s+\S+\s+\d+\s+\(phased (\d+)%\)', line)
            if mv:
                for p in pkgs:
                    if p["name"] == current_pkg and p["to"] == candidate:
                        p["phased_pct"] = int(mv.group(1))


def parse_dnf_security_pkgnames(out: str) -> set[str]:
    """Parse `dnf updateinfo list security` output → set of package names with security updates."""
    names: set[str] = set()
    skip = ("last metadata", "updating", "repositories", "security:", "")
    for line in out.splitlines():
        ls = line.strip().lower()
        if not ls or any(ls.startswith(s) for s in skip):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        pkg_nvra = parts[-1]                           # name-ver-rel.arch
        pkg_nvr  = pkg_nvra.rsplit(".", 1)[0]          # strip .arch
        m = re.match(r"^(.+?)-\d", pkg_nvr)           # strip -version
        if m:
            names.add(m.group(1))
    return names


def check_reboot_required(client: paramiko.SSHClient, sudo_pass: Optional[str] = None) -> bool:
    rc, out, _ = ssh_run(client, "test -f /var/run/reboot-required && echo yes || echo no", timeout=10, sudo_pass=sudo_pass)
    return "yes" in out


# ---------------------------------------------------------------------------
# DNF / RPM-family helpers
# ---------------------------------------------------------------------------

def _pkg_manager(os_name: str) -> str:
    """Return 'dnf' for Fedora/RHEL-family OSes, 'apt' otherwise."""
    dnf_keywords = ('fedora', 'rhel', 'centos', 'rocky', 'alma', 'oracle linux', 'red hat', 'amazon linux')
    return 'dnf' if any(k in (os_name or "").lower() for k in dnf_keywords) else 'apt'


def detect_os(client: paramiko.SSHClient) -> tuple[str, str]:
    """Return (human_os_name, pkg_mgr) by probing the remote host directly.

    The package manager is determined by which binary exists on the host;
    never inferred from the OS name, so unusual distros work automatically.
    """
    # ── Step 1: probe for the actual package manager ──────────────────────
    _, pm_out, _ = ssh_run(
        client,
        "if command -v dnf     >/dev/null 2>&1; then echo dnf; "
        "elif command -v apt-get >/dev/null 2>&1; then echo apt; "
        "elif command -v yum   >/dev/null 2>&1; then echo yum; "
        "else echo unknown; fi",
        timeout=10,
    )
    pm = pm_out.strip()
    # yum and dnf share the same command interface for our purposes
    pkg_mgr = "dnf" if pm in ("dnf", "yum") else "apt"

    # ── Step 2: read /etc/os-release for a human-readable name ───────────
    _, raw, _ = ssh_run(client, "cat /etc/os-release 2>/dev/null", timeout=10)
    if not raw.strip():
        return ("Unknown", pkg_mgr)

    fields: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            fields[k.strip()] = v.strip().strip('"')

    name       = fields.get("NAME", "")
    version_id = fields.get("VERSION_ID", "")
    ver_major  = version_id.split(".")[0] if version_id else ""
    name_l     = name.lower()

    # Format a short OS label (version_id already normalised above)
    if "ubuntu" in name_l:
        os_name = f"Ubuntu {version_id}".strip()
    elif "debian" in name_l:
        os_name = f"Debian {ver_major}".strip()
    elif "raspbian" in name_l or "raspberry" in name_l:
        os_name = f"Raspberry Pi OS {ver_major}".strip()
    elif "nobara" in name_l:
        os_name = f"Nobara Linux {ver_major}".strip()
    elif "fedora" in name_l:
        os_name = f"Fedora {ver_major}".strip()
    elif "rocky" in name_l:
        os_name = f"Rocky Linux {ver_major}".strip()
    elif "alma" in name_l:
        os_name = f"AlmaLinux {ver_major}".strip()
    elif "centos" in name_l:
        os_name = f"CentOS Stream {ver_major}".strip()
    elif "red hat" in name_l or "rhel" in name_l:
        os_name = f"RHEL {ver_major}".strip()
    elif "oracle" in name_l:
        os_name = f"Oracle Linux {ver_major}".strip()
    elif "amazon" in name_l:
        os_name = f"Amazon Linux {ver_major}".strip()
    else:
        os_name = f"{name} {ver_major}".strip() if name else "Unknown"

    return (os_name, pkg_mgr)


def _strip_epoch(ver: str) -> str:
    return ver.split(":", 1)[1] if ":" in ver else ver


def parse_dnf_upgradeable(upd_raw: str, inst_raw: str) -> list[dict]:
    """Parse `dnf check-update` (dnf4 or dnf5) + `rpm -qa` output into [{name, from, to}]."""
    installed: dict[str, str] = {}
    for line in inst_raw.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            installed[parts[0]] = parts[1]

    # Header/noise lines to skip (do NOT include '' (startswith('') is always True)
    skip_prefixes = (
        'last metadata', 'updated packages', 'available upgrades',
        'obsoleting', 'updating and loading', 'repositories loaded',
        'failed to', 'warning:', 'error:',
    )
    pkgs = []
    for line in upd_raw.splitlines():
        ls = line.strip().lower()
        if not ls:                                      # blank line
            continue
        if any(ls.startswith(s) for s in skip_prefixes):
            continue
        parts = line.split()
        # Package lines: name.arch  version  repo  (at least 2 fields, first has a dot)
        if len(parts) >= 2 and '.' in parts[0] and parts[0][0].isalpha():
            name = parts[0].rsplit('.', 1)[0]
            to_ver = _strip_epoch(parts[1])
            from_ver = _strip_epoch(installed.get(name, '?'))
            pkgs.append({"name": name, "to": to_ver, "from": from_ver})
    return pkgs


def check_reboot_required_dnf(client: paramiko.SSHClient, sudo_pass: Optional[str] = None) -> bool:
    """Check reboot-required on Fedora/RHEL: tries needs-restarting, falls back to kernel compare."""
    rc, out, _ = ssh_run(
        client,
        "if command -v needs-restarting >/dev/null 2>&1; then "
        "  needs-restarting -r >/dev/null 2>&1 && echo no || echo yes; "
        "else "
        "  RUNNING=$(uname -r); "
        "  LATEST=$(rpm -q kernel --queryformat '%{VERSION}-%{RELEASE}.%{ARCH}\\n' 2>/dev/null | sort -V | tail -1); "
        "  [ \"$RUNNING\" = \"$LATEST\" ] && echo no || echo yes; "
        "fi",
        timeout=15,
        sudo_pass=sudo_pass,
    )
    return "yes" in out


def _classify_apt_line(line: str) -> str:
    ll = line.lower()
    if "error" in ll or line.startswith("Err:"):
        return "error"
    if line.startswith("W:") or line.startswith("Ign:") or "kept back" in ll:
        return "warn"
    if line.startswith(("Get:", "Fetched")):
        return "info"
    if line.startswith(("Unpacking", "Setting up", "Processing", "Removing")):
        return "pkg"
    return "info"


def _classify_dnf_line(line: str) -> str:
    ll = line.lower()
    if ll.startswith("error") or "error:" in ll:
        return "error"
    if ll.startswith("warning") or "warning:" in ll:
        return "warn"
    if line.startswith(("Installing", "Upgrading", "Removing", "Replacing", "Cleanup", "Downgrading")):
        return "pkg"
    if "complete!" in ll or "nothing to do" in ll:
        return "ok"
    return "info"


async def _stream_cmd(client: paramiko.SSHClient, cmd: str, timeout: int, classify,
                      sudo_pass: Optional[str] = None):
    """Async generator: yields (line, level) pairs then a final ("__EXIT__", rc_str) sentinel."""
    if sudo_pass is not None:
        if sudo_pass:
            cmd = f"sudo -S -p '' sh -c {shlex.quote(cmd)}"
        else:
            cmd = f"sudo sh -c {shlex.quote(cmd)}"
    _, stdout, _ = client.exec_command(cmd, timeout=timeout, get_pty=True)
    channel = stdout.channel
    if sudo_pass:
        channel.sendall(f"{sudo_pass}\n".encode())
    buf = b""
    while True:
        chunk = await asyncio.get_event_loop().run_in_executor(None, lambda: channel.recv(4096))
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line_b, buf = buf.split(b"\n", 1)
            line = _strip_ansi(line_b.decode(errors="replace")).strip()
            if line:
                yield line, classify(line)
    rc = await asyncio.get_event_loop().run_in_executor(None, channel.recv_exit_status)
    yield "__EXIT__", str(rc)


# ---------------------------------------------------------------------------
# Email notifications
# ---------------------------------------------------------------------------

def _send_notification_sync(subject: str, body: str):
    db = get_db()
    cfg = {r["key"]: r["value"] for r in db.execute("SELECT key,value FROM settings")}
    db.close()
    relay    = cfg.get("smtp_relay", "").strip()
    to_email = cfg.get("notify_email", "").strip()
    if not relay or not to_email:
        return
    smtp_host, _, port_str = relay.partition(":")
    port = int(port_str) if port_str else 25
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"]    = "patchkit@localhost"
    msg["To"]      = to_email
    with smtplib.SMTP(smtp_host, port, timeout=15) as smtp:
        try:
            smtp.starttls()
        except smtplib.SMTPException:
            pass  # relay doesn't support STARTTLS; proceed plain
        smtp.sendmail("patchkit@localhost", [to_email], msg.as_string())


async def _send_notification(subject: str, body: str):
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: _send_notification_sync(subject, body)
        )
    except Exception as e:
        print(f"[patchkit] email failed: {e}")


def _send_webhook_sync(url: str, template: str, host_name: str,
                       result: str, pkg_count: int, duration: float):
    subs = {
        "{host}":         host_name,
        "{result}":       result,
        "{result_upper}": result.upper(),
        "{packages}":     str(pkg_count),
        "{duration}":     str(duration),
    }
    if template:
        body_str = template
        for k, v in subs.items():
            body_str = body_str.replace(k, v)
        payload = body_str.encode()
    else:
        payload = json.dumps({
            "title":       f"PatchKit: {host_name} - {result.upper()}",
            "message":     f"{pkg_count} package(s) upgraded in {duration}s",
            "host":        host_name,
            "result":      result,
            "packages":    pkg_count,
            "duration_s":  duration,
        }).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "PatchKit/1.0"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10)


async def _send_webhook(host_name: str, result: str, pkg_count: int, duration: float):
    db = get_db()
    cfg = {r["key"]: r["value"] for r in db.execute("SELECT key,value FROM settings")}
    db.close()
    url      = cfg.get("webhook_url", "").strip()
    template = cfg.get("webhook_template", "").strip()
    if not url:
        return
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: _send_webhook_sync(url, template, host_name, result, pkg_count, duration)
        )
    except Exception as e:
        print(f"[patchkit] webhook failed: {e}")


# ---------------------------------------------------------------------------
# Patch lock file (per-host)
# ---------------------------------------------------------------------------

def _lock_path(host_id: int) -> Path:
    return LOCK_DIR / f"patchkit-{host_id}.lock"


def _acquire_lock(host_id: int) -> bool:
    p = _lock_path(host_id)
    content = f"{os.getpid()} {datetime.now().isoformat()}".encode()

    def _try_create() -> bool:
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, content)
            os.close(fd)
            return True
        except FileExistsError:
            return False

    if _try_create():
        return True

    # Lock file exists: check if owning process is still alive
    try:
        pid = int(p.read_text().split()[0])
        os.kill(pid, 0)   # raises if process is gone
        return False       # process alive → locked
    except (ValueError, IndexError, ProcessLookupError, PermissionError):
        pass              # stale lock

    p.unlink(missing_ok=True)
    return _try_create()  # another process may win here; that's fine


def _release_lock(host_id: int):
    _lock_path(host_id).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Scan logic
# ---------------------------------------------------------------------------

async def scan_host_async(host_id: int) -> dict:
    async with _SCAN_SEM:
        db = get_db()
        row = db.execute("SELECT * FROM hosts WHERE id=?", (host_id,)).fetchone()
        if not row:
            db.close()
            raise HTTPException(404, "Host not found")
        defaults = {r["key"]: r["value"] for r in db.execute("SELECT key,value FROM settings")}
        sudo_pass = _resolve_sudo_pass(row, defaults)

        result: dict = {
            "host_id": host_id, "host_name": row["name"],
            "status": "offline", "pkg_count": 0, "packages": [],
            "reboot_req": False, "disk_used_pct": "", "disk_free": "",
            "uptime_str": "", "error": None,
        }

        try:
            client = await ssh_connect_async(row)
            try:
                loop = asyncio.get_event_loop()
                os_name_detected, pkg_mgr = await loop.run_in_executor(None, lambda: detect_os(client))

                # Persist detected OS name if it changed / was unknown
                if os_name_detected not in ("Unknown", "") and os_name_detected != row["os_name"]:
                    _db2 = get_db()
                    _db2.execute("UPDATE hosts SET os_name=? WHERE id=?", (os_name_detected, host_id))
                    _db2.commit()
                    _db2.close()

                if pkg_mgr == 'apt':
                    await loop.run_in_executor(
                        None, lambda: ssh_run(client, "apt-get update -qq 2>&1", timeout=60, sudo_pass=sudo_pass)
                    )
                    _, out, _ = await loop.run_in_executor(
                        None, lambda: ssh_run(client, "apt list --upgradeable 2>/dev/null", timeout=30, sudo_pass=sudo_pass)
                    )
                    pkgs = parse_upgradeable(out)
                    if pkgs:
                        names = [p["name"] for p in pkgs]
                        _, pol_out, _ = await loop.run_in_executor(
                            None, lambda: ssh_run(client, f"apt-cache policy {' '.join(shlex.quote(n) for n in names)} 2>/dev/null", timeout=30, sudo_pass=sudo_pass)
                        )
                        annotate_phased(pkgs, pol_out)
                    reboot = await loop.run_in_executor(None, lambda: check_reboot_required(client, sudo_pass))
                else:  # dnf
                    _, upd_out, _ = await loop.run_in_executor(
                        None, lambda: ssh_run(client, "dnf check-update 2>&1; exit 0", timeout=90, sudo_pass=sudo_pass)
                    )
                    _, inst_out, _ = await loop.run_in_executor(
                        None, lambda: ssh_run(client,
                            "rpm -qa --queryformat '%{NAME} %{VERSION}-%{RELEASE}\\n' 2>/dev/null", timeout=30, sudo_pass=sudo_pass)
                    )
                    pkgs = parse_dnf_upgradeable(upd_out, inst_out)
                    _, sec_out, _ = await loop.run_in_executor(
                        None, lambda: ssh_run(client,
                            "dnf updateinfo list security --quiet 2>&1; exit 0", timeout=30,
                            sudo_pass=sudo_pass)
                    )
                    sec_names = parse_dnf_security_pkgnames(sec_out)
                    # Detect when the distro has no advisory system (e.g. Nobara, vanilla Fedora).
                    # "No advisory found" means the metadata simply isn't there; don't mark
                    # packages as is_security=False when we have no data to back that up.
                    no_advisory_data = (
                        not sec_names and
                        any(phrase in sec_out.lower()
                            for phrase in ("no advisory found", "no match", "nothing to"))
                    )
                    if not no_advisory_data:
                        for p in pkgs:
                            p["is_security"] = p["name"] in sec_names
                    reboot = await loop.run_in_executor(None, lambda: check_reboot_required_dnf(client, sudo_pass))

                _, disk_out, _ = await loop.run_in_executor(
                    None, lambda: ssh_run(client, "df -h / 2>/dev/null | tail -1 | awk '{print $5, $4}'", timeout=10, sudo_pass=sudo_pass)
                )
                disk_parts = disk_out.strip().split()
                disk_used_pct = disk_parts[0] if disk_parts else ""
                disk_free     = disk_parts[1] if len(disk_parts) > 1 else ""

                _, up_out, _ = await loop.run_in_executor(
                    None, lambda: ssh_run(client, "uptime -p 2>/dev/null || uptime", timeout=10, sudo_pass=sudo_pass)
                )
                uptime_str = up_out.strip().splitlines()[0] if up_out.strip() else ""

                result.update({
                    "status": "warn" if pkgs else "ok",
                    "pkg_count": len(pkgs), "packages": pkgs,
                    "reboot_req": reboot,
                    "disk_used_pct": disk_used_pct, "disk_free": disk_free,
                    "uptime_str": uptime_str,
                })
            finally:
                client.close()
        except Exception as e:
            result["error"] = str(e) or type(e).__name__

        db.execute(
            """INSERT INTO scan_results
               (host_id, status, pkg_count, packages, reboot_req,
                disk_used_pct, disk_free, uptime_str, error)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (host_id, result["status"], result["pkg_count"],
             json.dumps(result["packages"]), int(result["reboot_req"]),
             result["disk_used_pct"], result["disk_free"],
             result["uptime_str"], result["error"] or ""),
        )
        db.commit()
        db.close()
        return result


# ---------------------------------------------------------------------------
# Patch logic (streaming SSE)
# ---------------------------------------------------------------------------

async def patch_host_stream(host_id: int):
    """Yields SSE lines while patching. Enforces per-host lock file."""
    if not _acquire_lock(host_id):
        yield "data: error|Host is already being patched (lock active)\n\n"
        yield "data: DONE\n\n"
        return

    db = get_db()
    row = db.execute("SELECT * FROM hosts WHERE id=?", (host_id,)).fetchone()
    if not row:
        db.close()
        _release_lock(host_id)
        yield f"data: error|Host {host_id} not found\n\n"
        yield "data: DONE\n\n"
        return

    defaults = {r["key"]: r["value"] for r in db.execute("SELECT key,value FROM settings")}
    sudo_pass = _resolve_sudo_pass(row, defaults)
    excluded = [p.strip() for p in (row["excluded_pkgs"] or "").split(",") if p.strip()]
    host_name = row["name"]
    t0 = time.time()
    log_lines: list[str] = []
    pkg_count = 0
    run_result = "ok"

    run_id = db.execute(
        "INSERT INTO patch_runs (host_ids, host_names) VALUES (?,?)",
        (json.dumps([host_id]), json.dumps([host_name])),
    ).lastrowid
    db.commit()

    def emit(msg: str, level: str = "info") -> str:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        log_lines.append(line)
        return f"data: {level}|{line}\n\n"

    if defaults.get("scan_before_patch") == "1":
        yield emit("Pre-scan: refreshing package list...")
        try:
            await scan_host_async(host_id)
            yield emit("Pre-scan complete", "ok")
        except Exception as e:
            yield emit(f"Pre-scan failed (continuing): {e}", "warn")

    yield emit(f"Connecting to {host_name} ({row['ip']})...")

    try:
        client = await ssh_connect_async(row)
        yield emit("Connected", "ok")

        loop = asyncio.get_event_loop()

        # Auto-detect OS and package manager
        os_detected, pkg_mgr = await loop.run_in_executor(None, lambda: detect_os(client))
        classify = _classify_apt_line if pkg_mgr == 'apt' else _classify_dnf_line
        sudo_label = "" if sudo_pass is None else (" (NOPASSWD sudo)" if not sudo_pass else " (sudo with password)")
        yield emit(f"Detected OS: {os_detected} → using {pkg_mgr}{sudo_label}", "info")

        # Persist if changed
        if os_detected not in ("Unknown", "") and os_detected != row["os_name"]:
            _db2 = get_db()
            _db2.execute("UPDATE hosts SET os_name=? WHERE id=?", (os_detected, host_id))
            _db2.commit()
            _db2.close()

        # ── Refresh metadata + list available upgrades ───────────────────
        yield emit("Checking upgradeable packages...")
        if pkg_mgr == 'apt':
            if excluded:
                yield emit(f"Holding excluded packages: {', '.join(excluded)}")
                await loop.run_in_executor(
                    None, lambda: ssh_run(client, f"apt-mark hold {' '.join(shlex.quote(p) for p in excluded)} 2>&1", timeout=30, sudo_pass=sudo_pass)
                )
            rc, _, err = await loop.run_in_executor(
                None, lambda: ssh_run(client, "DEBIAN_FRONTEND=noninteractive apt-get update -q 2>&1", 60, sudo_pass=sudo_pass)
            )
            if rc != 0:
                yield emit(f"apt-get update warning: {err.strip()}", "warn")
                run_result = "warn"
            else:
                yield emit("Package lists updated", "ok")
            _, out, _ = await loop.run_in_executor(
                None, lambda: ssh_run(client, "apt list --upgradeable 2>/dev/null", 30, sudo_pass=sudo_pass)
            )
            pkgs = parse_upgradeable(out)
        else:  # dnf: check-update refreshes metadata and lists in one shot
            yield emit("Running dnf check-update...")
            _, upd_out, _ = await loop.run_in_executor(
                None, lambda: ssh_run(client, "dnf check-update 2>&1; exit 0", 90, sudo_pass=sudo_pass)
            )
            _, inst_out, _ = await loop.run_in_executor(
                None, lambda: ssh_run(client,
                    "rpm -qa --queryformat '%{NAME} %{VERSION}-%{RELEASE}\\n' 2>/dev/null", 30, sudo_pass=sudo_pass)
            )
            pkgs = parse_dnf_upgradeable(upd_out, inst_out)

        pkg_count = len(pkgs)

        # ── Upgrade ───────────────────────────────────────────────────────
        if not pkgs:
            yield emit("Nothing to upgrade, already up to date", "ok")
        else:
            yield emit(f"Found {pkg_count} package(s) to upgrade")
            for p in pkgs:
                yield emit(f"  {p['name']}: {p['from']} → {p['to']}", "pkg")

            if pkg_mgr == 'apt':
                upgrade_cmd = (
                    "DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y "
                    "-o Dpkg::Options::='--force-confold' 2>&1"
                )
                autoremove_cmd = "DEBIAN_FRONTEND=noninteractive apt-get autoremove -y 2>&1"
                clean_cmd      = "apt-get clean 2>&1"
            else:
                if "nobara" in (os_detected or "").lower():
                    upgrade_cmd = "nobara-sync cli 2>&1"
                else:
                    excl_flags = " ".join(f"--exclude={shlex.quote(p)}" for p in excluded) if excluded else ""
                    upgrade_cmd = f"dnf upgrade -y {excl_flags} 2>&1".strip()
                autoremove_cmd = "dnf autoremove -y 2>&1"
                clean_cmd      = "dnf clean all 2>&1"

            yield emit("Applying upgrades...")
            upgrade_rc = None
            async for line, lv in _stream_cmd(client, upgrade_cmd, 300, classify, sudo_pass=sudo_pass):
                if line == "__EXIT__":
                    upgrade_rc = int(lv)
                else:
                    yield emit(line, lv)

            if upgrade_rc != 0:
                yield emit(f"Upgrade command exited with code {upgrade_rc}", "error")
                run_result = "error"
            else:
                # Verify what actually installed by re-checking pending packages
                yield emit("Verifying upgrade results...")
                if pkg_mgr == 'apt':
                    _, post_out, _ = await loop.run_in_executor(
                        None, lambda: ssh_run(client, "apt list --upgradeable 2>/dev/null", 30, sudo_pass=sudo_pass)
                    )
                    post_pkgs = {p["name"] for p in parse_upgradeable(post_out)}
                else:
                    _, post_upd_out, _ = await loop.run_in_executor(
                        None, lambda: ssh_run(client, "dnf check-update 2>&1; exit 0", 90, sudo_pass=sudo_pass)
                    )
                    _, post_inst_out, _ = await loop.run_in_executor(
                        None, lambda: ssh_run(client,
                            "rpm -qa --queryformat '%{NAME} %{VERSION}-%{RELEASE}\\n' 2>/dev/null", 30, sudo_pass=sudo_pass)
                    )
                    post_pkgs = {p["name"] for p in parse_dnf_upgradeable(post_upd_out, post_inst_out)}

                pre_names = {p["name"] for p in pkgs}
                actually_upgraded = pre_names - post_pkgs
                still_pending = pre_names & post_pkgs
                pkg_count = len(actually_upgraded)

                if actually_upgraded:
                    yield emit(f"Successfully upgraded {pkg_count} package(s)", "ok")
                if still_pending:
                    for name in sorted(still_pending):
                        yield emit(f"  {name}: skipped by package manager (dependency conflict or hold)", "warn")
                    run_result = "warn"
                if not actually_upgraded and not still_pending:
                    yield emit("Nothing was upgraded (already up to date)", "ok")

                yield emit("Checking for unneeded packages (autoremove)...")
                async for line, lv in _stream_cmd(client, autoremove_cmd, 120, classify, sudo_pass=sudo_pass):
                    if line == "__EXIT__":
                        if int(lv) != 0:
                            yield emit(f"autoremove exited with code {lv}", "warn")
                        else:
                            yield emit("Autoremove complete", "ok")
                    else:
                        yield emit(line, lv)

                yield emit("Cleaning package cache...")
                await loop.run_in_executor(None, lambda: ssh_run(client, clean_cmd, timeout=30, sudo_pass=sudo_pass))
                yield emit("Package cache cleaned", "ok")

        # ── Post-upgrade cleanup ──────────────────────────────────────────
        if pkg_mgr == 'apt' and excluded:
            await loop.run_in_executor(
                None, lambda: ssh_run(client, f"apt-mark unhold {' '.join(shlex.quote(p) for p in excluded)} 2>&1", timeout=30, sudo_pass=sudo_pass)
            )
            yield emit(f"Released holds on: {', '.join(excluded)}")

        if pkg_mgr == 'apt':
            reboot = await loop.run_in_executor(None, lambda: check_reboot_required(client, sudo_pass))
        else:
            reboot = await loop.run_in_executor(None, lambda: check_reboot_required_dnf(client, sudo_pass))
        if reboot:
            yield emit("Reboot required (kernel or libc updated)", "warn")

        client.close()

    except Exception as e:
        yield emit(f"SSH error: {e}", "error")
        run_result = "error"
    finally:
        _release_lock(host_id)

    duration = round(time.time() - t0, 1)
    db.execute(
        """UPDATE patch_runs SET finished_at=datetime('now'), pkg_count=?, result=?, duration_s=?, log=?
           WHERE id=?""",
        (pkg_count, run_result, duration, "\n".join(log_lines), run_id),
    )
    db.commit()
    db.close()
    yield emit(f"Done in {duration}s", "done")

    await _send_notification(
        f"PatchKit: {host_name} - {run_result.upper()}",
        f"Host: {host_name}\nResult: {run_result}\nPackages upgraded: {pkg_count}\n"
        f"Duration: {duration}s\n\nLog:\n" + "\n".join(log_lines),
    )
    await _send_webhook(host_name, run_result, pkg_count, duration)

    if run_result != "error":
        yield emit("Rescanning to verify...", "info")
        try:
            await scan_host_async(host_id)
            yield emit("Rescan complete", "ok")
        except Exception as e:
            yield emit(f"Rescan failed: {e}", "warn")

    yield "data: DONE\n\n"


# ---------------------------------------------------------------------------
# APScheduler
# ---------------------------------------------------------------------------



async def _reload_all_schedules():
    db = get_db()
    rows = db.execute("SELECT * FROM schedules WHERE enabled=1").fetchall()
    db.close()
    for row in rows:
        _register_schedule_job(row)


def _register_schedule_job(row: sqlite3.Row):
    cron_expr = (row["cron_expr"] or "").strip()
    if not cron_expr:
        return
    job_id = f"sched_{row['id']}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    parts = cron_expr.split()
    if len(parts) != 5:
        return
    try:
        trigger = CronTrigger.from_crontab(cron_expr)
        host_ids = json.loads(row["host_ids"] or "[]")
        scheduler.add_job(
            _run_scheduled_patch,
            trigger=trigger,
            id=job_id,
            args=[row["id"], host_ids],
            replace_existing=True,
            misfire_grace_time=3600,
        )
        job = scheduler.get_job(job_id)
        if job and job.next_run_time:
            db2 = get_db()
            db2.execute("UPDATE schedules SET next_run=? WHERE id=?",
                        (job.next_run_time.strftime("%Y-%m-%d %H:%M"), row["id"]))
            db2.commit()
            db2.close()
    except Exception as e:
        print(f"[patchkit] schedule {row['id']} registration failed: {e}")


async def _run_scheduled_patch(schedule_id: int, host_ids: list[int]):
    db = get_db()
    db.execute("UPDATE schedules SET last_run=datetime('now') WHERE id=?", (schedule_id,))
    db.commit()
    row = db.execute("SELECT tags FROM schedules WHERE id=?", (schedule_id,)).fetchone()
    tags = [t.strip() for t in (row["tags"] if row else "" or "").split(",") if t.strip()]
    if tags:
        tag_hosts = set()
        for h in db.execute("SELECT id, tags FROM hosts WHERE enabled=1"):
            host_tags = [t.strip() for t in (h["tags"] or "").split(",") if t.strip()]
            if any(t in host_tags for t in tags):
                tag_hosts.add(h["id"])
        host_ids = list(set(host_ids) | tag_hosts)
    if not host_ids:
        host_ids = [r["id"] for r in db.execute("SELECT id FROM hosts WHERE enabled=1")]
    db.close()
    async def _drain(hid: int):
        async for _ in patch_host_stream(hid):
            pass

    await asyncio.gather(*[_drain(hid) for hid in host_ids])
    job = scheduler.get_job(f"sched_{schedule_id}")
    if job and job.next_run_time:
        db2 = get_db()
        db2.execute("UPDATE schedules SET next_run=? WHERE id=?",
                    (job.next_run_time.strftime("%Y-%m-%d %H:%M"), schedule_id))
        db2.commit()
        db2.close()


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/hosts")
def list_hosts():
    db = get_db()
    hosts = [dict(h) for h in db.execute("SELECT * FROM hosts ORDER BY name")]

    # Build last-patched map from patch_runs
    runs = db.execute(
        "SELECT host_ids, finished_at FROM patch_runs WHERE result='ok' AND finished_at IS NOT NULL"
    ).fetchall()
    last_patched: dict[int, str] = {}
    for run in runs:
        for hid in json.loads(run["host_ids"] or "[]"):
            if run["finished_at"] > last_patched.get(hid, ""):
                last_patched[hid] = run["finished_at"]

    for h in hosts:
        scan = db.execute(
            "SELECT * FROM scan_results WHERE host_id=? ORDER BY id DESC LIMIT 1",
            (h["id"],)
        ).fetchone()
        if scan:
            h["scan"] = {
                "status":       scan["status"],
                "pkg_count":    scan["pkg_count"],
                "packages":     json.loads(scan["packages"]),
                "reboot_req":   bool(scan["reboot_req"]),
                "scanned_at":   scan["scanned_at"],
                "disk_used_pct": scan["disk_used_pct"] or "",
                "disk_free":    scan["disk_free"] or "",
                "uptime_str":   scan["uptime_str"] or "",
                "error":        scan["error"] or "",
            }
        else:
            h["scan"] = None
        h["last_patched"] = last_patched.get(h["id"])
    db.close()
    return hosts


@app.get("/api/groups")
def list_groups():
    """Return all unique tags with host/package counts and member host IDs."""
    db = get_db()
    hosts = [dict(h) for h in db.execute("SELECT * FROM hosts WHERE enabled=1 ORDER BY name")]
    for h in hosts:
        scan = db.execute(
            "SELECT pkg_count, status FROM scan_results WHERE host_id=? ORDER BY id DESC LIMIT 1",
            (h["id"],)
        ).fetchone()
        h["_pkg_count"] = scan["pkg_count"] if scan else 0
    db.close()

    groups: dict[str, dict] = {}
    for h in hosts:
        tags = [t.strip() for t in (h.get("tags") or "").split(",") if t.strip()]
        for tag in tags:
            if tag not in groups:
                groups[tag] = {"tag": tag, "host_count": 0, "pkg_count": 0, "host_ids": []}
            groups[tag]["host_count"] += 1
            groups[tag]["pkg_count"]  += h["_pkg_count"]
            groups[tag]["host_ids"].append(h["id"])

    return sorted(groups.values(), key=lambda g: g["tag"])


def _hosts_for_tag(tag: str) -> list[int]:
    db = get_db()
    hosts = [dict(h) for h in db.execute("SELECT id, tags FROM hosts WHERE enabled=1")]
    db.close()
    return [h["id"] for h in hosts
            if tag in [t.strip() for t in (h.get("tags") or "").split(",") if t.strip()]]


@app.post("/api/scan-group/{tag}")
async def scan_group(tag: str):
    ids = _hosts_for_tag(tag)
    results = await asyncio.gather(*[scan_host_async(hid) for hid in ids], return_exceptions=True)
    return [r if isinstance(r, dict) else {"error": str(r)} for r in results]


@app.get("/api/rolling-reboot/{tag}")
async def rolling_reboot_stream(tag: str, grace: int = 30):
    """Reboot hosts in a group one at a time, waiting for each to recover before proceeding."""
    ids = _hosts_for_tag(tag)

    async def stream():
        db = get_db()
        hosts = [dict(db.execute("SELECT * FROM hosts WHERE id=?", (hid,)).fetchone())
                 for hid in ids]
        defaults = {r["key"]: r["value"] for r in db.execute("SELECT key,value FROM settings")}
        db.close()

        def emit(msg: str, level: str = "info") -> str:
            ts = datetime.now().strftime("%H:%M:%S")
            return f"data: {level}|[{ts}] {msg}\n\n"

        yield emit(f"Rolling reboot: {len(hosts)} host(s) in group", "info")

        for i, host in enumerate(hosts, 1):
            name = host["name"]
            ip   = host["ip"]
            host_sudo = _resolve_sudo_pass(host, defaults)
            yield emit(f"[{i}/{len(hosts)}] Rebooting {name} ({ip})...", "info")

            # Send reboot
            try:
                client = await ssh_connect_async(host, max_attempts=1)
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ssh_run(client, "reboot", timeout=5, sudo_pass=host_sudo)
                )
                client.close()
            except Exception:
                pass  # connection drop on reboot is expected

            # Wait for SSH to go down (confirms reboot started)
            yield emit(f"  Waiting for {name} to go offline...", "info")
            deadline = asyncio.get_event_loop().time() + 120
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(5)
                try:
                    r, w = await asyncio.wait_for(
                        asyncio.open_connection(ip, int(host.get("port", 22))),
                        timeout=3
                    )
                    w.close()
                    await w.wait_closed()
                except Exception:
                    break  # connection refused/timeout = node is down

            yield emit(f"  {name} is offline, waiting for recovery...", "warn")

            # Wait for SSH to come back
            deadline = asyncio.get_event_loop().time() + 300
            recovered = False
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(10)
                try:
                    r, w = await asyncio.wait_for(
                        asyncio.open_connection(ip, int(host.get("port", 22))),
                        timeout=5
                    )
                    w.close()
                    await w.wait_closed()
                    recovered = True
                    break
                except Exception:
                    continue

            if not recovered:
                yield emit(f"  {name} did not recover within 5 minutes, stopping", "error")
                yield "data: DONE\n\n"
                return

            # Grace period for services to stabilise
            yield emit(f"  {name} is back online, waiting {grace}s for services...", "ok")
            await asyncio.sleep(grace)

            # Rescan to clear reboot_req flag
            yield emit(f"  Rescanning {name}...", "info")
            try:
                await scan_host_async(host["id"])
                yield emit(f"  {name} ready", "ok")
            except Exception as e:
                yield emit(f"  Rescan failed: {e}", "warn")

        yield emit("Rolling reboot complete", "done")
        yield "data: DONE\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/patch-group/{tag}")
async def patch_group_stream(tag: str):
    ids = _hosts_for_tag(tag)

    async def combined():
        for hid in ids:
            async for chunk in patch_host_stream(hid):
                yield chunk

    return StreamingResponse(
        combined(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/hosts", status_code=201)
def add_host(body: HostCreate):
    db = get_db()
    try:
        db.execute(
            """INSERT INTO hosts (name,ip,port,ssh_user,ssh_key,os_name,role,tags,excluded_pkgs,notes,sudo_pass,credential_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (body.name, body.ip, body.port, body.ssh_user, body.ssh_key,
             body.os_name, body.role, body.tags, body.excluded_pkgs, body.notes,
             body.sudo_pass, body.credential_id),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.close()
        raise HTTPException(409, f"Host '{body.name}' already exists")
    hid = db.execute("SELECT id FROM hosts WHERE name=?", (body.name,)).fetchone()["id"]
    db.close()
    return {"id": hid, "name": body.name}


_ALLOWED_HOST_COLUMNS = {"name", "ip", "port", "ssh_user", "ssh_key", "os_name", "role", "tags", "excluded_pkgs", "notes", "sudo_pass", "credential_id", "enabled"}


@app.patch("/api/hosts/{host_id}")
def update_host(host_id: int, body: HostUpdate):
    db = get_db()
    fields = {k: v for k, v in body.dict().items() if v is not None and k in _ALLOWED_HOST_COLUMNS}
    # Empty sudo_pass on PATCH means "keep existing"
    fields.pop("sudo_pass", None) if fields.get("sudo_pass") == "" else None
    # credential_id=0 means "clear credential" → store NULL
    if fields.get("credential_id") == 0:
        fields["credential_id"] = None
    if not fields:
        db.close()
        return {"ok": True}
    set_clause = ", ".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE hosts SET {set_clause} WHERE id=?", (*fields.values(), host_id))
    db.commit()
    db.close()
    return {"ok": True}


@app.delete("/api/hosts/{host_id}")
def delete_host(host_id: int):
    db = get_db()
    db.execute("DELETE FROM hosts WHERE id=?", (host_id,))
    db.commit()
    db.close()
    return {"ok": True}


@app.post("/api/hosts/bulk", status_code=201)
def bulk_import_hosts(body: BulkImport):
    db = get_db()
    added, skipped = [], []
    for h in body.hosts:
        try:
            db.execute(
                """INSERT INTO hosts (name,ip,port,ssh_user,ssh_key,os_name,role,tags,excluded_pkgs,notes,sudo_pass,credential_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (h.name, h.ip, h.port, h.ssh_user, h.ssh_key, "", "", h.tags, "", "", "", h.credential_id),
            )
            db.commit()
            added.append(h.name)
        except sqlite3.IntegrityError:
            skipped.append(h.name)
    db.close()
    return {"added": added, "skipped": skipped}


@app.get("/api/hosts/{host_id}/ping")
def ping_host(host_id: int):
    db = get_db()
    row = db.execute("SELECT ip, port FROM hosts WHERE id=?", (host_id,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "Host not found")
    ip, port = row["ip"], int(row["port"] or 22)
    t0 = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=3):
            pass
        return {"reachable": True, "latency_ms": int((time.monotonic() - t0) * 1000)}
    except Exception:
        return {"reachable": False, "latency_ms": None}


# ---------------------------------------------------------------------------
# Credential routes
# ---------------------------------------------------------------------------

@app.get("/api/credentials")
def list_credentials():
    db = get_db()
    rows = [dict(r) for r in db.execute("SELECT * FROM credentials ORDER BY name")]
    for r in rows:
        r["fingerprint"] = _key_fingerprint(r.get("ssh_key", ""))
        r["host_count"] = db.execute(
            "SELECT COUNT(*) FROM hosts WHERE credential_id=?", (r["id"],)
        ).fetchone()[0]
        del r["ssh_key"]   # don't expose key content in list
        del r["sudo_pass"] # don't expose password in list
    db.close()
    return rows


@app.get("/api/credentials/{cid}")
def get_credential(cid: int):
    db = get_db()
    row = db.execute("SELECT * FROM credentials WHERE id=?", (cid,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "Credential not found")
    r = dict(row)
    r["fingerprint"] = _key_fingerprint(r.get("ssh_key", ""))
    return r


@app.post("/api/credentials", status_code=201)
def add_credential(body: CredentialCreate):
    if body.ssh_key.strip():
        try:
            _load_key_content(body.ssh_key)
        except Exception as e:
            raise HTTPException(400, f"Invalid SSH key: {e}")
    db = get_db()
    try:
        db.execute(
            "INSERT INTO credentials (name, ssh_user, ssh_key, sudo_pass) VALUES (?,?,?,?)",
            (body.name, body.ssh_user, body.ssh_key, body.sudo_pass),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.close()
        raise HTTPException(409, f"Credential '{body.name}' already exists")
    cid = db.execute("SELECT id FROM credentials WHERE name=?", (body.name,)).fetchone()["id"]
    db.close()
    return {"id": cid, "name": body.name}


@app.patch("/api/credentials/{cid}")
def update_credential(cid: int, body: CredentialUpdate):
    db = get_db()
    fields: dict = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.ssh_user is not None:
        fields["ssh_user"] = body.ssh_user
    if body.ssh_key is not None and body.ssh_key.strip():
        try:
            _load_key_content(body.ssh_key)
        except Exception as e:
            db.close()
            raise HTTPException(400, f"Invalid SSH key: {e}")
        fields["ssh_key"] = body.ssh_key
    if body.sudo_pass is not None:
        fields["sudo_pass"] = body.sudo_pass
    if fields:
        set_clause = ", ".join(f"{k}=?" for k in fields)
        db.execute(f"UPDATE credentials SET {set_clause} WHERE id=?", (*fields.values(), cid))
        db.commit()
    db.close()
    return {"ok": True}


@app.delete("/api/credentials/{cid}")
def delete_credential(cid: int):
    db = get_db()
    db.execute("UPDATE hosts SET credential_id=NULL WHERE credential_id=?", (cid,))
    db.execute("DELETE FROM credentials WHERE id=?", (cid,))
    db.commit()
    db.close()
    return {"ok": True}


@app.get("/api/credentials/{cid}/test")
def test_credential(cid: int):
    db = get_db()
    cred = db.execute("SELECT * FROM credentials WHERE id=?", (cid,)).fetchone()
    if not cred:
        db.close()
        raise HTTPException(404, "Credential not found")
    hosts = db.execute("SELECT * FROM hosts WHERE credential_id=? AND enabled=1", (cid,)).fetchall()
    defaults = {r["key"]: r["value"] for r in db.execute("SELECT key,value FROM settings")}
    db.close()

    if not hosts:
        return {"results": [], "message": "No hosts assigned to this credential"}

    try:
        pkey = _load_key_content(cred["ssh_key"])
    except Exception as e:
        return {"results": [], "error": f"Failed to load key: {e}"}

    results = []
    for h in hosts:
        port    = int(h["port"] or defaults.get("ssh_port", 22))
        user    = cred["ssh_user"] or defaults.get("ssh_user", "root")
        timeout = int(defaults.get("ssh_timeout", 10))
        try:
            client = _make_ssh_client()
            client.connect(
                hostname=h["ip"], port=port, username=user, pkey=pkey,
                timeout=timeout, banner_timeout=timeout, auth_timeout=timeout,
                look_for_keys=False, allow_agent=False,
            )
            _, out, _ = client.exec_command("echo ok", timeout=5)
            ok = out.read().decode().strip() == "ok"
            client.close()
            results.append({"host": h["name"], "ip": h["ip"], "ok": ok, "error": None})
        except Exception as e:
            results.append({"host": h["name"], "ip": h["ip"], "ok": False, "error": str(e)})

    return {"results": results}


@app.get("/api/hosts/{host_id}/test")
def test_host(host_id: int):
    import traceback, stat
    db = get_db()
    row = db.execute("SELECT * FROM hosts WHERE id=?", (host_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Host not found")
    defaults = {r["key"]: r["value"] for r in db.execute("SELECT key,value FROM settings")}
    cred = db.execute("SELECT * FROM credentials WHERE id=?", (row["credential_id"],)).fetchone() \
           if row["credential_id"] else None
    db.close()

    user    = (cred["ssh_user"] if cred else row["ssh_user"]) or defaults.get("ssh_user", "root")
    port    = int(row["port"] or defaults.get("ssh_port", 22))
    timeout = int(defaults.get("ssh_timeout", 10))
    steps = {
        "host": row["name"], "ip": row["ip"], "port": port, "user": user,
        "credential": cred["name"] if cred else None,
        "key_path": None, "key_exists": None,
        "key_stat": None, "key_load": None, "connect": None, "error": None,
    }

    if cred:
        steps["key_path"] = f"[credential: {cred['name']}]"
        steps["key_exists"] = True
        try:
            pkey = _load_key_content(cred["ssh_key"])
            steps["key_load"] = "ok"
        except Exception as e:
            steps["key_load"] = "FAILED"
            steps["error"] = str(e)
            return steps
    else:
        key_path = _expand_key(row["ssh_key"] or defaults.get("ssh_key", "~/.ssh/id_ed25519"))
        steps["key_path"] = str(key_path)
        steps["key_exists"] = key_path.exists()
        if not key_path.exists():
            steps["error"] = f"Key file not found: {key_path}"
            return steps
        s = key_path.stat()
        steps["key_stat"] = oct(stat.S_IMODE(s.st_mode))
        try:
            pkey = _load_key(key_path)
            steps["key_load"] = "ok"
        except Exception as e:
            steps["key_load"] = "FAILED"
            steps["error"] = str(e)
            return steps

    try:
        client = _make_ssh_client()
        client.connect(
            hostname=row["ip"], port=port, username=user, pkey=pkey,
            timeout=timeout, banner_timeout=timeout, auth_timeout=timeout,
            look_for_keys=False, allow_agent=False,
        )
        _save_host_key(client)
        _, out, _ = client.exec_command("echo ok", timeout=5)
        steps["connect"] = out.read().decode().strip()
        client.close()
    except Exception:
        steps["connect"] = "FAILED"
        steps["error"] = traceback.format_exc()
    return steps


@app.post("/api/detect-os")
async def detect_os_direct(body: TempHostSpec):
    """Detect OS from connection details without a stored host record."""
    fake: dict = {"ip": body.ip, "port": body.port, "ssh_user": body.ssh_user,
                  "ssh_key": body.ssh_key, "name": "temp"}
    try:
        client = await ssh_connect_async(fake, max_attempts=1)  # type: ignore[arg-type]
        loop = asyncio.get_event_loop()
        os_name, pkg_mgr = await loop.run_in_executor(None, lambda: detect_os(client))
        client.close()
        return {"os_name": os_name, "pkg_mgr": pkg_mgr}
    except Exception as e:
        raise HTTPException(503, str(e))


@app.post("/api/hosts/{host_id}/detect-os")
async def detect_host_os(host_id: int):
    """Detect and persist the OS name for an existing host."""
    db = get_db()
    row = db.execute("SELECT * FROM hosts WHERE id=?", (host_id,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "Host not found")
    try:
        client = await ssh_connect_async(row, max_attempts=1)
        loop = asyncio.get_event_loop()
        os_name, pkg_mgr = await loop.run_in_executor(None, lambda: detect_os(client))
        client.close()
    except Exception as e:
        raise HTTPException(503, str(e))
    if os_name not in ("Unknown", ""):
        db2 = get_db()
        db2.execute("UPDATE hosts SET os_name=? WHERE id=?", (os_name, host_id))
        db2.commit()
        db2.close()
    return {"os_name": os_name, "pkg_mgr": pkg_mgr}


@app.post("/api/hosts/{host_id}/scan")
async def scan_host(host_id: int):
    return await scan_host_async(host_id)


@app.get("/api/hosts/{host_id}/reboot")
async def reboot_host_stream(host_id: int):
    """SSE stream: send reboot, wait for host to go down and come back, then rescan."""
    async def stream():
        db = get_db()
        row = db.execute("SELECT * FROM hosts WHERE id=?", (host_id,)).fetchone()
        defaults = {r["key"]: r["value"] for r in db.execute("SELECT key,value FROM settings")}
        db.close()
        if not row:
            yield "data: error|Host not found\n\ndata: DONE\n\n"
            return

        ip   = row["ip"]
        port = int(row["port"] or 22)
        sudo_pass = _resolve_sudo_pass(row, defaults)
        reboot_timeout = int(defaults.get("reboot_timeout", 300))

        def emit(msg: str, level: str = "info") -> str:
            ts = datetime.now().strftime("%H:%M:%S")
            return f"data: {level}|[{ts}] {msg}\n\n"

        yield emit(f"Sending reboot command to {row['name']} ({ip})...")
        try:
            client = await ssh_connect_async(row, max_attempts=1)
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: ssh_run(client, "reboot", timeout=5, sudo_pass=sudo_pass)
            )
            client.close()
        except Exception:
            pass  # connection drop on reboot is expected

        yield emit("Waiting for host to go offline...")
        deadline = asyncio.get_event_loop().time() + 120
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(5)
            try:
                r, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=3)
                w.close()
                await w.wait_closed()
            except Exception:
                break

        yield emit(f"Host is offline, waiting for recovery (timeout: {reboot_timeout}s)...", "warn")
        deadline = asyncio.get_event_loop().time() + reboot_timeout
        recovered = False
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(10)
            try:
                r, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=5)
                w.close()
                await w.wait_closed()
                recovered = True
                break
            except Exception:
                continue

        if not recovered:
            yield emit("Host did not come back within 5 minutes", "error")
            yield "data: DONE\n\n"
            return

        yield emit("Host is back online, rescanning...", "ok")
        await asyncio.sleep(10)
        try:
            await scan_host_async(host_id)
            yield emit("Rescan complete, reboot flag cleared", "ok")
        except Exception as e:
            yield emit(f"Rescan failed: {e}", "warn")

        yield "data: DONE\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/hosts/{host_id}/reboot")
async def reboot_host(host_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM hosts WHERE id=?", (host_id,)).fetchone()
    defaults = {r["key"]: r["value"] for r in db.execute("SELECT key,value FROM settings")}
    db.close()
    if not row:
        raise HTTPException(404, "Host not found")
    sudo_pass = _resolve_sudo_pass(row, defaults)
    try:
        client = await ssh_connect_async(row, max_attempts=1)
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: ssh_run(client, "reboot", timeout=5, sudo_pass=sudo_pass)
        )
        client.close()
    except Exception:
        pass  # connection drop on reboot is expected

    reboot_timeout = int(defaults.get("reboot_timeout", 300))
    asyncio.create_task(_rescan_after_reboot(host_id, row["ip"], int(row["port"] or 22), reboot_timeout))
    return {"ok": True}


async def _rescan_after_reboot(host_id: int, ip: str, port: int, timeout: int = 300):
    """Wait for SSH to come back after reboot, then trigger a rescan."""
    await asyncio.sleep(15)  # give the system time to start shutting down
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(10)
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=5)
            w.close()
            await w.wait_closed()
            break  # SSH port is back up
        except Exception:
            continue
    else:
        return  # timed out, skip rescan

    await asyncio.sleep(10)  # brief settle time before scanning
    try:
        await scan_host_async(host_id)
    except Exception:
        pass


@app.get("/api/hosts/{host_id}/locked")
def is_host_locked(host_id: int):
    p = _lock_path(host_id)
    if not p.exists():
        return {"locked": False}
    try:
        parts = p.read_text().split()
        pid = int(parts[0])
        os.kill(pid, 0)
        return {"locked": True, "since": parts[1] if len(parts) > 1 else ""}
    except (ValueError, IndexError, ProcessLookupError, PermissionError):
        return {"locked": False}


@app.get("/api/hosts/{host_id}/history")
def get_host_history(host_id: int, limit: int = 20):
    db = get_db()
    runs = [dict(r) for r in db.execute(
        "SELECT * FROM patch_runs ORDER BY id DESC LIMIT 200"
    )]
    db.close()
    filtered = []
    for r in runs:
        try:
            if host_id in json.loads(r.get("host_ids") or "[]"):
                r["host_names"] = json.loads(r["host_names"] or "[]")
                filtered.append(r)
        except Exception:
            pass
    return filtered[:limit]


@app.post("/api/scan-all")
async def scan_all():
    db = get_db()
    ids = [r["id"] for r in db.execute("SELECT id FROM hosts WHERE enabled=1")]
    db.close()
    results = await asyncio.gather(
        *[scan_host_async(hid) for hid in ids], return_exceptions=True
    )
    return [r if isinstance(r, dict) else {"error": str(r)} for r in results]


@app.get("/api/hosts/{host_id}/patch")
async def patch_host(host_id: int):
    return StreamingResponse(
        patch_host_stream(host_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/patch-all")
async def patch_all():
    db = get_db()
    ids = [r["id"] for r in db.execute("SELECT id FROM hosts WHERE enabled=1")]
    db.close()

    async def combined():
        for hid in ids:
            async for chunk in patch_host_stream(hid):
                yield chunk

    return StreamingResponse(
        combined(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/history")
def get_history(limit: int = 50):
    limit = min(max(limit, 1), 500)
    db = get_db()
    runs = [dict(r) for r in db.execute(
        "SELECT * FROM patch_runs ORDER BY id DESC LIMIT ?", (limit,)
    )]
    db.close()
    for r in runs:
        r["host_names"] = json.loads(r["host_names"] or "[]")
    return runs


@app.get("/api/history/{run_id}/log")
def get_run_log(run_id: int):
    db = get_db()
    row = db.execute("SELECT log FROM patch_runs WHERE id=?", (run_id,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404)
    return {"log": row["log"]}


@app.get("/api/schedules")
def list_schedules():
    db = get_db()
    rows = [dict(r) for r in db.execute("SELECT * FROM schedules ORDER BY id")]
    db.close()
    for r in rows:
        r["host_ids"] = json.loads(r["host_ids"] or "[]")
        r["tags"] = r.get("tags") or ""
    return rows


@app.post("/api/schedules")
def add_schedule(body: dict):
    db = get_db()
    db.execute(
        "INSERT INTO schedules (name, host_ids, tags, cron_expr, enabled, next_run) VALUES (?,?,?,?,?,?)",
        (body["name"], json.dumps(body.get("host_ids", [])),
         body.get("tags", ""), body.get("cron_expr", ""), 1, "")
    )
    db.commit()
    sid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = db.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
    db.close()
    _register_schedule_job(row)
    return {"ok": True, "id": sid}


@app.patch("/api/schedules/{sid}")
def update_schedule(sid: int, body: dict):
    db = get_db()
    fields: dict = {}
    if "name" in body:
        fields["name"] = body["name"]
    if "cron_expr" in body:
        fields["cron_expr"] = body["cron_expr"]
    if "host_ids" in body:
        fields["host_ids"] = json.dumps(body["host_ids"])
    if "tags" in body:
        fields["tags"] = body["tags"]
    if "enabled" in body:
        fields["enabled"] = body["enabled"]
    if fields:
        set_clause = ", ".join(f"{k}=?" for k in fields)
        db.execute(f"UPDATE schedules SET {set_clause} WHERE id=?", (*fields.values(), sid))
    db.commit()
    row = db.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
    db.close()
    if row:
        try:
            scheduler.remove_job(f"sched_{sid}")
        except Exception:
            pass
        if row["enabled"]:
            _register_schedule_job(row)
    return {"ok": True}


@app.delete("/api/schedules/{sid}")
def delete_schedule(sid: int):
    try:
        scheduler.remove_job(f"sched_{sid}")
    except Exception:
        pass
    db = get_db()
    db.execute("DELETE FROM schedules WHERE id=?", (sid,))
    db.commit()
    db.close()
    return {"ok": True}


@app.get("/api/settings")
def get_settings():
    db = get_db()
    rows = db.execute("SELECT key,value FROM settings").fetchall()
    db.close()
    return {r["key"]: r["value"] for r in rows}


@app.post("/api/settings")
def save_settings(body: SettingsPayload):
    db = get_db()
    for k, v in body.settings.items():
        db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, v))
    db.commit()
    db.close()
    _reload_auth_header()
    _reload_autoscan_job()
    return {"ok": True}


@app.post("/api/settings/test-webhook")
async def test_webhook(body: dict):
    url      = (body.get("url") or "").strip()
    template = (body.get("template") or "").strip()
    if not url:
        raise HTTPException(400, "No webhook URL provided")
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: _send_webhook_sync(url, template, "test-host", "ok", 0, 0.0)
        )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(400, str(e))




@app.get("/api/version")
def get_version():
    return {"version": APP_VERSION, "changelog": CHANGELOG}


@app.get("/api/me")
def get_me(request: Request):
    if not _AUTH_HEADER:
        return {"enabled": False, "user": None, "name": None, "email": None}
    return {
        "enabled": True,
        "user":  request.headers.get(_AUTH_HEADER, ""),
        "name":  request.headers.get("X-Authentik-Name", ""),
        "email": request.headers.get("X-Authentik-Email", ""),
    }


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index():
    return Path("static/index.html").read_text()


app.mount("/static", StaticFiles(directory="static"), name="static")
