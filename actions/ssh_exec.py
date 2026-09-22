"""
actions/ssh_exec.py — MIAs SSH-Werkzeug fuer ihre drei Server.

Server werden per Name angesprochen (brain, control, knowledge). Authentifizierung
ueber den Cluster-Key /root/.ssh/mia_cluster_ed25519 (auf allen drei Hosts
autorisiert). Laeuft auf dem Brain-Host, wo main.py laeuft - nicht im
Command-Center-Container (dort gibt es kein ssh/scp).

Sicherheitsmodell (vom Nutzer so gewuenscht: "nur mit meiner Berechtigung"):
  - REINE LESE-Befehle (ls, cat, df, docker ps, systemctl status, journalctl,
    tail, grep, ...) laufen sofort, ohne Rueckfrage.
  - ALLES ANDERE (alles, was aendern, loeschen, installieren, starten/stoppen
    kann) geht ueber das Bestaetigungs-Gate core/confirm.py: MIA schlaegt vor,
    der Nutzer bestaetigt auf dem Dashboard, erst dann laeuft der Befehl.
    Ohne gebundene Oberflaeche (Hintergrund-Worker, lokaler Textpfad) wird
    so ein Befehl automatisch abgelehnt - ehrlich gemeldet, nie still ausgefuehrt.
"""
from __future__ import annotations

import shlex
import subprocess

from core import confirm as _confirm

HOSTS = {
    "brain":     "127.0.0.1",
    "control":   "31.70.140.208",
    "knowledge": "31.70.152.166",
}
KEY = "/root/.ssh/mia_cluster_ed25519"
TIMEOUT = 60
OUTPUT_LIMIT = 4000

# Erste Wort(e) eines Befehls, die als rein lesend gelten.
_READ_ONLY = {
    "ls", "cat", "head", "tail", "wc", "df", "du", "free", "uptime", "hostname",
    "whoami", "date", "pwd", "find", "grep", "stat", "file", "ps", "top", "uname",
    "ss", "ip", "nproc", "lscpu", "lsblk", "mount", "env",
}
_READ_ONLY_PREFIX = (
    "docker ps", "docker images", "docker logs", "docker inspect", "docker stats --no-stream",
    "systemctl status", "systemctl is-active", "systemctl list-units", "systemctl list-timers",
    "journalctl", "git status", "git log", "git diff", "ollama list", "curl -s",
)


def _is_read_only(cmd: str) -> bool:
    c = cmd.strip()
    if any(tok in c for tok in ("|", ";", "&&", "||", ">", "<", "$(", "`", "sudo ", "rm ", "dd ")):
        # Verkettungen/Umleitungen koennen schreiben -> immer bestaetigen lassen
        return False
    if any(c.startswith(p) for p in _READ_ONLY_PREFIX):
        return True
    try:
        first = shlex.split(c)[0]
    except Exception:
        return False
    return first in _READ_ONLY


def _run(host_ip: str, cmd: str) -> str:
    try:
        proc = subprocess.run(
            ["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
             "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
             "-o", "LogLevel=ERROR", f"root@{host_ip}", cmd],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
        out = (proc.stdout + proc.stderr).strip()
        if len(out) > OUTPUT_LIMIT:
            out = out[:OUTPUT_LIMIT] + "\n... [gekuerzt]"
        return f"Exit {proc.returncode}.\n{out}" if out else f"Exit {proc.returncode}. Keine Ausgabe."
    except subprocess.TimeoutExpired:
        return f"Befehl nach {TIMEOUT}s abgebrochen (Timeout)."
    except Exception as e:
        return f"SSH fehlgeschlagen: {e}"


def ssh_exec(parameters: dict, player=None, session_memory=None) -> str:
    host = str(parameters.get("host", "")).strip().lower()
    cmd = str(parameters.get("command", "")).strip()
    if host not in HOSTS:
        return f"Unbekannter Server '{host}'. Erlaubt: brain, control, knowledge."
    if not cmd:
        return "Kein Befehl angegeben."
    ip = HOSTS[host]

    if _is_read_only(cmd):
        result = _run(ip, cmd)
    else:
        result = _confirm.request(
            key=f"ssh_{host}_{abs(hash(cmd))}",
            title=f"MIA will auf Server '{host}' einen aendernden Befehl ausfuehren",
            detail=f"{host} ({ip}): {cmd}",
            run=lambda: _run(ip, cmd),
        )

    if player:
        try:
            player.write_log(f"MIA: [ssh {host}] {cmd[:60]} -> {result[:120]}")
        except Exception:
            pass
    return result


TOOL = {
    "name": "ssh_exec",
    "description": (
        "Run a shell command on one of MIA's own servers via SSH: host must be 'brain' "
        "(this machine), 'control' or 'knowledge'. Read-only commands (ls, cat, df, docker ps, "
        "systemctl status, journalctl, git log ...) run immediately. Any command that could change "
        "something is put behind an on-screen confirmation the user must press - never claim it ran "
        "before the tool result says so. Use this instead of trying to copy files or 'get access'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "host": {"type": "STRING", "description": "brain | control | knowledge"},
            "command": {"type": "STRING", "description": "The shell command to run"},
        },
        "required": ["host", "command"],
    },
    "handler": ssh_exec,
}
