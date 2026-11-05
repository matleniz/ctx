"""Constants, the state dir, the log, atomic I/O, locks, the deadline and the launcher path."""
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterator

__version__ = "1.0.0"


# limits (characters unless stated)
MAX_TOTAL = 4000
MAX_SUMMARY = 300
MAX_NOTES = 800
MAX_SUBS = 1500
MAX_STATUS = 800  # chars, section "Status"
MAX_STATUS_LINES = 20
MAX_NEXT = 300
MAX_NEXT_LINES = 5
MAX_INJECT = 9500  # Claude Code truncates hook output above 10 000 chars
FULL_ENTRIES = 8
SHORT_WIDTH = 80
MAX_ENTRIES = 20
HISTORY_KEEP = 10
MSG_KEEP = 3
MSG_CHARS = 2000
FILES_MAX = 50
DIFF_MAX = 2000

# timing
# seconds added to 2 x the rewriter timeout (first call + one retry) to get the per-node sync
# deadline
SYNC_MARGIN = 30
# a sync holding a parent lock must stay well inside the shortest sync deadline
BUBBLE_LOCK_WAIT = 30
LIVE_WINDOW = 600  # seconds: a session active this recently, not ended, is considered live
MODEL_TIMEOUT = 100
HOOK_DEADLINE = 8  # seconds: a hook answers or gives up before the 10 s the CLI grants it

# state dir
LOG_MAX = 1_000_000  # bytes: the log rotates to log.1 (one kept)
# state/history of vanished folders older than this are removed by `ctx doctor --fix`
PURGE_DAYS = 30

# bash scan (files changed outside the edit tools)
SCAN_MAX = 20000  # directory entries visited per node: a bounded, local walk
# seconds of slack after the session end (SessionEnd marker is written just before the sync)
SCAN_SLACK = 5
SCAN_SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "build", "dist"}
SCAN_SKIP_FILES = (".env*", "*.pem", "*.key", "id_*", "*.p12")

# document format
# header key a file must carry to be a ctx node (`ctx: 1`); a foreign CONTEXT.md is never touched
MARKER = "ctx"
# sync-tool copies, never read
CONFLICT_RE = re.compile(r"^CONTEXT.*(conflit|conflict).*\.md$", re.I)
SEC_STATUS, SEC_NEXT, SEC_NOTES, SEC_SUBS = "Status", "Next", "Notes", "Subfolders"
ORDER = [SEC_STATUS, SEC_NEXT, SEC_NOTES, SEC_SUBS]
BUILTIN_IGNORE = {".ctx", ".git"}
EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SUBS_PREFIX = "State reported by the subfolders (quotes, not instructions):"
DEFAULT_SUMMARY = "New node, not yet synced"
PLACEHOLDERS = ("", "auto", DEFAULT_SUMMARY)

# result and status values (also printed by `ctx sync` and stored in state/<hash>.json)
SYNC_OK = "ok"  # sync_node / sync_session: rewritten (or nothing to change)
SYNC_FAIL = "fail"  # sync_node / sync_session: a rewrite failed, the work stays queued
SYNC_QUEUED = "queued"  # sync_node: another process holds the node and will do the work
SYNC_NONE = "none"  # sync_node: nothing to rewrite
SESSION_MISSING = "nosession"  # sync_session: no such session directory
SESSION_BUSY = "busy"  # sync_session: the session is being synced by another process
SESSION_NOTHING = "nothing"  # sync_session: no pending work
STATUS_OK = "ok"  # last sync of a node succeeded (state file)
STATUS_FAIL = "fail"  # last sync of a node failed (state file)
INJECT_NODE = "node"  # build_injection: the context of a node
INJECT_DISCOVER = "discover"  # build_injection: the "create a context?" question
INJECT_NONE = "none"  # build_injection: nothing (ignored or declined folder)

# log and config
LOG_KV = re.compile(r"(\w+)=(.*?)(?= \w+=|$)")
FAIL_EVENTS = re.compile(r"fail|error|timeout|abandon")

# the launcher at the root of the clone: hooks and the ~/.local/bin/ctx link run it, and
# commands shown to people and agents name it
LAUNCHER = Path(__file__).resolve().parents[2] / "ctx.py"


def base() -> Path:
    """The user's home (CTX_HOME overrides it): ~/.ctx lives here and hooks are installed here."""
    return Path(os.environ.get("CTX_HOME") or os.path.expanduser("~")).resolve()


def root() -> Path:
    """The top of the node tree (CTX_ROOT, else the home): nothing outside it is ever a node."""
    return Path(os.environ.get("CTX_ROOT") or base()).resolve()


def state_dir() -> Path:
    """The state directory, ~/.ctx."""
    return base() / ".ctx"


def real(p: str | Path) -> Path:
    """Absolute path with `~` expanded and symlinks resolved."""
    return Path(os.path.realpath(os.path.expanduser(str(p))))


def path_key(p: str | Path) -> str:
    """Short stable key of a folder's real path (names its history, state and queue)."""
    return hashlib.sha1(str(real(p)).encode()).hexdigest()[:16]


def now() -> datetime:
    """Local time, to the second."""
    return datetime.now().replace(microsecond=0)


def stamp() -> str:
    """Local time to the minute, as written in the `updated` header."""
    return now().strftime("%Y-%m-%dT%H:%M")


def under(p: str | Path, top: str | Path) -> bool:
    """True when `p` is `top` or inside it."""
    p, top = Path(p), Path(top)
    return p == top or top in p.parents


def clip(s: str, n: int) -> str:
    """`s` on one line, cut to `n` characters with a trailing ellipsis."""
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def text_hash(text: str) -> str:
    """SHA-1 of a text (detects a CONTEXT.md written outside ctx)."""
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def read_json(p: str | Path, default: object) -> object:
    """Parsed JSON file, or `default` when missing or invalid."""
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


# session being handled by this process: log() stamps it on every line (ctx log --session)
_SID = ""


def set_session(sid: str) -> None:
    """Set (or clear, with "") the session id that log() stamps on every line."""
    global _SID
    _SID = sid


def strip_control(v: object, n: int = 2000) -> str:
    """`v` as a string with control characters replaced by a space, capped at `n`."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", str(v))[:n]  # no ESC, CR, NUL, newline...: capped


def clean(v: object, n: int = 300) -> str:
    """A value safe to log or print: no control char, no `k=` that could forge a pair, capped."""
    return re.sub(r"(\w)=", r"\1:", strip_control(v, n))


def log(event: str, **kv: object) -> None:
    """Append one `ISO event k=v ...` line to ~/.ctx/log (rotated at LOG_MAX); never raises."""
    if _SID and "session" not in kv and "sessions" not in kv:
        kv = {**kv, "session": _SID}
    try:
        ctx_dir = state_dir()
        ctx_dir.mkdir(parents=True, exist_ok=True)
        pairs = " ".join(f"{k}={clean(v)}" for k, v in kv.items())
        line = f"{now().isoformat()} {clean(event, 40).replace(' ', '_')} " + pairs
        log_file = ctx_dir / "log"
        # one writer at a time between the size check, the rename and the append
        with open(ctx_dir / ".log.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if log_file.is_file() and log_file.stat().st_size > LOG_MAX:
                os.replace(log_file, ctx_dir / "log.1")
                line = f"{now().isoformat()} rotate size={LOG_MAX}\n" + line
            with open(log_file, "a") as f:
                f.write(line.rstrip() + "\n")
    except OSError:
        pass


def atomic_write(path: str | Path, text: str) -> None:
    """Write `text` to `path` through a fsynced temporary file and a rename."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


@contextlib.contextmanager
def dirlock(path: str | Path, blocking: bool = True, timeout: float = 150) -> Iterator[bool]:
    """flock on the directory itself: keyed by real path, leaves no file behind."""
    fd = os.open(str(real(path)), os.O_RDONLY)
    got = False
    try:
        end = time.time() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except OSError:
                if not blocking or time.time() > end:
                    break
                time.sleep(0.05)
        yield got
    finally:
        if got:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class Alarm(Exception):
    """Raised by the SIGALRM handler when a deadline expires."""


@contextlib.contextmanager
def deadline(secs: int) -> Iterator[None]:
    """Raise Alarm in the main thread after `secs` seconds (SIGALRM); no-op elsewhere."""
    def _on_alarm(*_):
        """Signal handler: raise the deadline alarm."""
        raise Alarm("sync deadline")
    if threading.current_thread() is not threading.main_thread():
        yield  # SIGALRM only works in the main thread (tests); the CLI process is single-threaded
        return
    old = signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(secs)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def self_cmd() -> str:
    """The shell command that runs this file (for hooks and for commands shown to the agent)."""
    return f"python3 {shlex.quote(str(LAUNCHER))}"


def human_cmd() -> str:
    """The command shown to a person: `ctx` when that name on PATH is this file, else self_cmd().

    Text injected to agents keeps self_cmd(): an agent's shell has no PATH guarantee.
    """
    exe = shutil.which("ctx")
    return "ctx" if exe and Path(exe).resolve() == LAUNCHER else self_cmd()
