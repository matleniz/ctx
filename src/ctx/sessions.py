"""Hook collection, pending work, session liveness, catch-up."""
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

from .paths import (
    EDIT_TOOLS, LAUNCHER, LIVE_WINDOW, MSG_CHARS, MSG_KEEP, PID_LIVE_MAX, atomic_write, log,
    read_json, real, stamp, state_dir,
)
from .filters import redact

SHELLS = {"sh", "dash", "bash", "zsh", "env"}  # wrappers between the CLI and the hook process


def session_dir_of(sid: str) -> Path:
    """The state directory of a session (its id made safe as a folder name)."""
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(sid))[:100].strip(".") or "unknown"
    return state_dir() / "sessions" / sid


def touch_session(event: dict, cwd: str | None = None) -> Path:
    """Create the session dir on first sight and record the CLI pid; returns the dir.

    The dir records the launch cwd; the pid is the client process that runs the hooks.
    """
    session_dir = session_dir_of(event.get("session_id", ""))
    session_dir.mkdir(parents=True, exist_ok=True)
    if not (session_dir / "cwd").exists():
        (session_dir / "cwd").write_text(str(real(cwd or event.get("cwd") or os.getcwd())))
    record_pid(session_dir)
    return session_dir


def collect_start(event: dict) -> None:
    """SessionStart: create the session's directory."""
    touch_session(event)


def collect_tool(event: dict) -> None:
    """PostToolUse: record the path of a file written by an edit tool."""
    tool_input = event.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if event.get("tool_name") not in EDIT_TOOLS or not file_path:
        return
    cwd = event.get("cwd") or os.getcwd()
    file_path = os.path.normpath(os.path.join(cwd, file_path))
    if "\n" in file_path or "\r" in file_path:  # one path per line: a newline would forge entries
        log("touched-skipped", session=event.get("session_id", ""), why="newline in path")
        return
    with open(touch_session(event, cwd) / "touched", "a") as f:
        f.write(file_path + "\n")


def collect_stop(event: dict) -> None:
    """Stop: keep the agent's last report (redacted), the last MSG_KEEP of them."""
    msg = event.get("last_assistant_message")
    if not msg:
        return
    session_dir = touch_session(event)
    msgs = read_json(session_dir / "last.json", [])
    redacted, nred = redact(str(msg)[:MSG_CHARS])  # secrets never reach ~/.ctx state files
    if nred:
        log("filtered", session=event.get("session_id", ""), count=nred,
            why="secrets in session report (stop hook)")
    msgs.append(redacted)
    atomic_write(session_dir / "last.json", json.dumps(msgs[-MSG_KEEP:]))


class Pending(NamedTuple):
    """What a session has not synced yet (see session_pending)."""

    files: list  # touched files not yet synced
    msgs: list  # session reports to send (empty when neither files nor reports are new)
    cwd: str  # launch folder ('' when unknown)
    state: dict  # synced.json: offset, last, fails, error, scanned
    nlines: int  # number of lines in `touched`: the next offset once synced
    msgs_hash: str  # hash of the stored reports: the next `last` once synced


def session_pending(session_dir: Path) -> Pending:
    """The session's work not yet synced, as a Pending tuple."""
    state = read_json(session_dir / "synced.json", {"offset": 0, "last": "", "fails": 0})
    lines = []
    if (session_dir / "touched").is_file():
        lines = (session_dir / "touched").read_text().splitlines()
    new = lines[state["offset"]:]
    msgs = read_json(session_dir / "last.json", [])
    h = hashlib.sha1(json.dumps(msgs).encode()).hexdigest() if msgs else ""
    new_msg = h != state["last"] and bool(msgs)
    cwd = (session_dir / "cwd").read_text().strip() if (session_dir / "cwd").is_file() else ""
    return Pending(new, msgs if new_msg or new else [], cwd, state, len(lines), h)


def iter_sessions() -> list[Path]:
    """Session directories under ~/.ctx/sessions, in name order."""
    top = state_dir() / "sessions"
    return [d for d in sorted(top.iterdir()) if d.is_dir()] if top.is_dir() else []


def abandoned() -> list[tuple[str, str, str]]:
    """Sessions given up after 2 failed syncs with work still pending: [(id, cwd, cause)]."""
    out = []
    for session_dir in iter_sessions():
        pending = session_pending(session_dir)
        if pending.state.get("fails", 0) >= 2 and (pending.files or pending.msgs):
            out.append((session_dir.name, pending.cwd, pending.state.get("error", "unknown")))
    return out


def proc_start(pid: int) -> str:
    """Start time (clock ticks since boot, /proc/<pid>/stat field 22) or '' when unavailable."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        # fields after "(comm)": state is index 0 -> starttime is 19
        return raw[raw.rindex(")") + 2:].split()[19]
    except (OSError, ValueError, IndexError):
        return ""


def cli_pid() -> int:
    """The CLI that runs the hooks: our parent past the `sh -c` wrapper (dash does not exec, and
    that short-lived sh made running sessions look dead, caught up before their Stop hook)."""
    pid = os.getppid()
    for _ in range(3):
        try:
            raw = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return pid  # no /proc (macOS): the parent
        if raw[raw.index("(") + 1:raw.rindex(")")] not in SHELLS:
            return pid
        pid = int(raw[raw.rindex(")") + 2:].split()[1])
    return pid


def record_pid(session_dir: Path) -> None:
    """Remember the CLI process that launches the hooks (cli_pid).

    Its start time is kept too, as a guard against pid reuse.
    """
    pid = cli_pid()
    if pid <= 1:
        return
    line = f"{pid} {proc_start(pid)}".strip()
    with contextlib.suppress(OSError):
        if (session_dir / "pid").read_text().strip() == line:
            return
    atomic_write(session_dir / "pid", line + "\n")


def pid_dead(session_dir: Path) -> bool:
    """True only when the recorded CLI process is certainly gone.

    Unknown or unreadable means False (the LIVE_WINDOW fallback decides).
    """
    try:
        parts = (session_dir / "pid").read_text().split()
        pid = int(parts[0])
    except (OSError, ValueError, IndexError):
        return False
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        pass  # exists, owned by someone else
    except OSError:
        return False
    if len(parts) > 1 and parts[1]:
        cur = proc_start(pid)
        if cur and cur != parts[1]:
            return True  # the pid was reused by another process
    return False


def sess_mtime(session_dir: Path) -> float:
    """Latest modification time of the session directory and its files."""
    return max([x.stat().st_mtime for x in [session_dir, *session_dir.iterdir()]] or [0])


def session_live(session_dir: Path) -> bool:
    """True while the session is still running.

    Not ended and CLI process alive (idle at most PID_LIVE_MAX); without a pid file, active within
    LIVE_WINDOW.
    """
    if (session_dir / "ended").exists() or pid_dead(session_dir):
        return False
    window = PID_LIVE_MAX if (session_dir / "pid").is_file() else LIVE_WINDOW
    return time.time() - sess_mtime(session_dir) < window


def catch_up(exclude: object = None, spawn: bool = True, skip_live: bool = False,
             close: bool = True) -> list[str]:
    """Find sessions with unsynced work and sync them (detached); returns their ids.

    skip_live leaves running sessions alone. Finished sessions older than a week are removed.
    """
    found = []
    for session_dir in iter_sessions():
        pending = session_pending(session_dir)
        has_work = bool(pending.files or pending.msgs)
        fails = pending.state.get("fails", 0)
        done_or_abandoned = not has_work or fails >= 2
        if time.time() - sess_mtime(session_dir) > 7 * 86400 and done_or_abandoned:
            # finished, or abandoned and left for a week
            shutil.rmtree(session_dir, ignore_errors=True)
            continue
        if (session_dir / "closed").exists() and not (session_dir / "pid").exists():
            try:
                closed_at = int((session_dir / "closed").read_text().split()[-1])
            except (OSError, ValueError, IndexError):
                closed_at = pending.nlines
            if pending.nlines <= closed_at:
                # pid-less session already caught up once; it comes back only if it touched more
                # files
                continue
        if not pending.files and (session_dir / "synced.json").exists():
            # only new reports on a session already marked synced: no file to route, no re-sync
            continue
        if skip_live and has_work and session_live(session_dir):
            continue
        if has_work and fails < 2:
            found.append(session_dir.name)
            if close and not (session_dir / "pid").exists():
                atomic_write(session_dir / "closed", f"{stamp()} {pending.nlines}\n")
            if spawn:
                spawn_sync(session_dir.name)
    if found:
        log("catchup", sessions=",".join(found))
    return found


def spawn_sync(sid: str) -> None:
    """Start `ctx sync --session <sid>` detached from this process."""
    if not sid:
        return
    subprocess.Popen([sys.executable, str(LAUNCHER), "sync", "--session", sid],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True, env=dict(os.environ))
