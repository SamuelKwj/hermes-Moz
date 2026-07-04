from __future__ import annotations

import csv
import os
import socket
import subprocess
from contextlib import closing


def _address_uses_port(address: str, port: int) -> bool:
    return address.strip().rsplit(":", 1)[-1] == str(port)


def _process_name(pid: str) -> str:
    if os.name != "nt":
        return ""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return ""
    output = result.stdout.strip()
    if not output or output.upper().startswith("INFO:"):
        return ""
    try:
        return next(csv.reader(output.splitlines()))[0]
    except Exception:
        return ""


def find_port_owners(port: int) -> list[str]:
    if os.name != "nt":
        return []
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return []

    owners: list[str] = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local_address, state, pid = parts[1], parts[-2].upper(), parts[-1]
        if state != "LISTENING" or not _address_uses_port(local_address, port) or pid in seen:
            continue
        seen.add(pid)
        name = _process_name(pid)
        owners.append(f"{name or '未知进程'}(PID {pid})")
    return owners


def format_port_in_use_message(app_name: str, host: str, port: int, owners: list[str] | None = None) -> str:
    owner_text = "、".join(owners or []) if owners else "未能识别占用进程"
    return (
        f"端口 {port} 已被占用，{app_name} 无法在 http://{host}:{port} 启动。"
        f"占用进程：{owner_text}。请关闭该进程或释放端口；不会自动切换端口。"
    )


def assert_port_available(host: str, port: int, app_name: str) -> None:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with closing(socket.socket(family, socket.SOCK_STREAM)) as sock:
            sock.bind((host, port))
    except OSError as exc:
        raise RuntimeError(format_port_in_use_message(app_name, host, port, find_port_owners(port))) from exc
