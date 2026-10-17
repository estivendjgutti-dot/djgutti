"""Tarefas executáveis pelo worker.

Cada tarefa recebe um TaskContext e devolve um dict (resultado, gravado como JSON).
Erros:
- TransientError: rede, timeout, HTTP 408/429/5xx -> nova tentativa com backoff.
- PermanentError: configuração inválida, HTTP 4xx -> falha visível sem retry.
Tarefas com transactional=True rodam dentro da mesma transação que marca o run
como concluído, então uma queda no meio não deixa efeito local pela metade.
"""

import email.utils
import os
import re
import socket
import time
import urllib.error
import urllib.request

import db


class TransientError(Exception):
    def __init__(self, message, retry_after=None, network=False):
        super().__init__(message)
        self.retry_after = retry_after
        self.network = network


class PermanentError(Exception):
    pass


class TaskTimeout(Exception):
    pass


_SECRET_RE = re.compile(
    r"(?i)\b(token|api[_-]?key|key|secret|password|senha|passwd|authorization|cookie)\b(\s*[=:]\s*)([^\s&,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_URL_QUERY_RE = re.compile(r"(https?://[^\s?#]+)[?#][^\s]*")
_URL_USERINFO_RE = re.compile(r"(https?://)[^/\s@]+@")


def sanitize(text, limit=300):
    """Remove credenciais óbvias, query strings e userinfo de URLs; trunca."""
    if text is None:
        return None
    text = str(text).replace("\n", " ").replace("\r", " ")
    text = _URL_USERINFO_RE.sub(r"\1***@", text)
    text = _URL_QUERY_RE.sub(r"\1?***", text)
    text = _BEARER_RE.sub(lambda m: m.group(1) + " ***", text)
    text = _SECRET_RE.sub(lambda m: m.group(1) + m.group(2) + "***", text)
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def parse_retry_after(value, now=None, cap=3600):
    """Retry-After em segundos ou data HTTP -> segundos (limitado a cap)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return min(int(value), cap)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=db.utcnow().tzinfo)
    delta = (when - (now or db.utcnow())).total_seconds()
    return max(0, min(int(delta), cap))


class TaskContext(object):
    def __init__(self, conn, run, job, params):
        self.conn = conn
        self.run = run
        self.job = job
        self.params = params


# --- tarefas ----------------------------------------------------------------


def demo_timestamp(ctx):
    """Tarefa local: registra o horário da execução (efeito idempotente por run_id)."""
    now = db.to_iso(db.utcnow())
    ctx.conn.execute(
        "INSERT INTO demo_events(run_id, job_id, scheduled_at_utc, recorded_at_utc) VALUES (?, ?, ?, ?)",
        (ctx.run["id"], ctx.run["job_id"], ctx.run["scheduled_at_utc"], now),
    )
    return {"recorded_at_utc": now}


def http_check(ctx):
    """GET somente leitura em URL pública configurada; não envia dados."""
    url = (ctx.params.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise PermanentError("url ausente ou inválida (use http:// ou https://)")
    timeout = float(ctx.params.get("http_timeout_seconds", 10))
    expected = ctx.params.get("expect_status")
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "estiven4tech-mobile-server/1"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(65536)
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        retry_after = parse_retry_after(exc.headers.get("Retry-After") if exc.headers else None)
        exc.close()
        if status in (408, 425, 429) or status >= 500:
            raise TransientError("HTTP %d" % status, retry_after=retry_after)
        raise PermanentError("HTTP %d" % status)
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise TransientError("rede indisponível ou timeout: %s" % type(reason).__name__, network=True)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if expected is not None and int(expected) != status:
        raise PermanentError("HTTP %d (esperado %s)" % (status, expected))
    return {"http_status": status, "elapsed_ms": elapsed_ms}


def backup_local(ctx):
    import backup

    path = backup.create_backup(reason="agendado")
    removed = backup.apply_retention()
    return {"backup": os.path.basename(path), "removed": len(removed)}


def test_sleep(ctx):
    """Somente para testes: simula uma tarefa demorada (ex.: queda no meio)."""
    time.sleep(float(ctx.params.get("seconds", 5)))
    return {"slept": ctx.params.get("seconds", 5)}


class Task(object):
    def __init__(self, func, transactional=False, network=False):
        self.func = func
        self.transactional = transactional
        self.network = network


REGISTRY = {
    "demo_timestamp": Task(demo_timestamp, transactional=True),
    "http_check": Task(http_check, network=True),
    "backup_local": Task(backup_local),
    "test_sleep": Task(test_sleep),
}
