"""Worker único: agendador integrado + fila durável SQLite + execução (concorrência 1).

Agenda reconstruída do banco a cada início (jobs.last_slot_utc), nunca da memória.
Slots alinhados ao epoch UTC: um job de 300 s roda em :00, :05, :10...
Esperas usam relógio monotônico; agenda usa UTC.
"""

import fcntl
import json
import logging
import math
import os
import random
import signal
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402
import tasks  # noqa: E402

PROJECT_DIR = db.PROJECT_DIR
CATCHUP_POLICIES = ("latest", "skip", "window")
MAX_SKIPPED_ROWS = 288  # um dia de slots de 5 min: registro de lacunas sem inchar o banco
LEASE_GRACE_SECONDS = 30
EXIT_LOCKED = 3
EXIT_WATCHDOG = 70

log = logging.getLogger("worker")


# --- configuração -------------------------------------------------------------


def load_env_file(path=None):
    """Lê .env (KEY=VALUE). Variáveis já definidas no ambiente têm prioridade."""
    path = path or os.path.join(PROJECT_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def env_flag(name):
    return os.environ.get(name, "0").strip().lower() in ("1", "true", "yes", "sim")


def jobs_config_path():
    explicit = os.environ.get("E4T_JOBS_FILE")
    if explicit:
        return explicit
    local = os.path.join(PROJECT_DIR, "config", "jobs.json")
    return local if os.path.exists(local) else os.path.join(PROJECT_DIR, "config", "jobs.example.json")


def load_jobs(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    min_interval = 1 if env_flag("E4T_ALLOW_SHORT_INTERVALS") else 30
    jobs = []
    for raw in data.get("jobs", []):
        job_id = str(raw["job_id"])
        kind = raw["kind"]
        if kind not in tasks.REGISTRY:
            log.error("job %s: kind desconhecido %r; ignorado", job_id, kind)
            continue
        if kind == "test_sleep" and not env_flag("E4T_ALLOW_SHORT_INTERVALS"):
            log.error("job %s: test_sleep só é permitido em testes; ignorado", job_id)
            continue
        interval = int(raw["interval_seconds"])
        if interval < min_interval:
            log.error("job %s: interval_seconds < %d; ignorado", job_id, min_interval)
            continue
        catchup = raw.get("catchup", "latest")
        if catchup not in CATCHUP_POLICIES:
            log.error("job %s: catchup %r inválido; usando 'latest'", job_id, catchup)
            catchup = "latest"
        params = raw.get("params", {})
        enabled = bool(raw.get("enabled", False))
        if enabled and kind == "http_check" and not str(params.get("url", "")).startswith(("http://", "https://")):
            log.warning("job %s: http_check sem url válida; mantido desativado", job_id)
            enabled = False
        jobs.append({
            "job_id": job_id,
            "kind": kind,
            "enabled": 1 if enabled else 0,
            "interval_seconds": interval,
            "catchup": catchup,
            "catchup_window_seconds": int(raw.get("catchup_window_seconds", 0)),
            "max_lateness_seconds": raw.get("max_lateness_seconds"),
            "max_attempts": max(1, min(int(raw.get("max_attempts", 5)), 5)),
            "timeout_seconds": max(1, int(raw.get("timeout_seconds", 30))),
            "external_effect": 1 if raw.get("external_effect", False) else 0,
            "params_json": json.dumps(params, sort_keys=True),
        })
    return jobs


def sync_jobs(conn, jobs):
    """Grava definições no banco; jobs ausentes do arquivo são desativados (histórico preservado)."""
    now = db.to_iso(db.utcnow())
    conn.execute("BEGIN IMMEDIATE")
    try:
        for j in jobs:
            conn.execute(
                """INSERT INTO jobs(job_id, kind, enabled, interval_seconds, catchup, catchup_window_seconds,
                       max_lateness_seconds, max_attempts, timeout_seconds, external_effect, params_json,
                       updated_at_utc)
                   VALUES (:job_id, :kind, :enabled, :interval_seconds, :catchup, :catchup_window_seconds,
                       :max_lateness_seconds, :max_attempts, :timeout_seconds, :external_effect, :params_json, :now)
                   ON CONFLICT(job_id) DO UPDATE SET
                       kind=excluded.kind, enabled=excluded.enabled, interval_seconds=excluded.interval_seconds,
                       catchup=excluded.catchup, catchup_window_seconds=excluded.catchup_window_seconds,
                       max_lateness_seconds=excluded.max_lateness_seconds, max_attempts=excluded.max_attempts,
                       timeout_seconds=excluded.timeout_seconds, external_effect=excluded.external_effect,
                       params_json=excluded.params_json, updated_at_utc=excluded.updated_at_utc""",
                dict(j, now=now),
            )
        ids = [j["job_id"] for j in jobs]
        placeholders = ",".join("?" * len(ids)) or "''"
        conn.execute(
            "UPDATE jobs SET enabled = 0, updated_at_utc = ? WHERE enabled = 1 AND job_id NOT IN (%s)" % placeholders,
            [now] + ids,
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# --- agenda ---------------------------------------------------------------------


def plan_slots(job, now_ts):
    """Decide quais slots enfileirar e quais registrar como pulados.

    Retorna (pendentes, pulados, novo_last_slot) em epoch segundos.
    """
    interval = job["interval_seconds"]
    current = int(math.floor(now_ts / interval) * interval)
    last = db.from_iso(job["last_slot_utc"])
    last = int(last.timestamp()) if last else None

    if last is not None and current < last - interval:
        log.warning("job %s: relógio voltou (slot %d < último %d); agenda reiniciada", job["job_id"], current, last)
        last = None
    if last is None:
        return [current], [], current
    if current <= last:
        return [], [], last

    missed = list(range(last + interval, current + interval, interval))
    policy = job["catchup"]
    if policy == "window":
        window = max(job["catchup_window_seconds"], interval)
        pending = [s for s in missed if now_ts - s <= window][-50:]
    elif policy == "skip":
        grace = job["max_lateness_seconds"] if job["max_lateness_seconds"] is not None else min(interval, 60)
        pending = [current] if now_ts - current <= grace else []
    else:  # latest
        pending = [current]
    pending_set = set(pending)
    skipped = [s for s in missed if s not in pending_set][-MAX_SKIPPED_ROWS:]
    return pending, skipped, current


def schedule_due(conn, now_ts):
    now_iso = db.to_iso(db.from_epoch(now_ts))
    jobs = conn.execute("SELECT * FROM jobs WHERE enabled = 1").fetchall()
    for job in jobs:
        pending, skipped, new_last = plan_slots(job, now_ts)
        if not pending and not skipped and db.to_iso(db.from_epoch(new_last)) == job["last_slot_utc"]:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            for s in pending:
                insert_run(conn, job["job_id"], s, "pending", now_iso)
            for s in skipped:
                insert_run(conn, job["job_id"], s, "skipped", now_iso, error="slot perdido (worker parado/atrasado)")
            conn.execute("UPDATE jobs SET last_slot_utc = ? WHERE job_id = ?",
                         (db.to_iso(db.from_epoch(new_last)), job["job_id"]))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        if skipped:
            log.warning("job %s: %d slot(s) perdido(s) registrados como skipped (política %s)",
                        job["job_id"], len(skipped), job["catchup"])


def insert_run(conn, job_id, slot_ts, status, now_iso, error=None):
    sched = db.to_iso(db.from_epoch(slot_ts))
    # INSERT OR IGNORE + UNIQUE(job_id, scheduled_at_utc): nunca duplica um slot.
    conn.execute(
        "INSERT OR IGNORE INTO runs(job_id, scheduled_at_utc, status, idempotency_key, error, created_at_utc, "
        "finished_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (job_id, sched, status, "%s@%s" % (job_id, sched), error, now_iso,
         now_iso if status == "skipped" else None),
    )


# --- execução -------------------------------------------------------------------


def backoff_seconds(attempt, base=30, cap=3600):
    """Exponencial com jitter: base*2^(n-1), sorteado entre 50% e 100%."""
    ceiling = min(cap, base * (2 ** max(0, attempt - 1)))
    return ceiling * (0.5 + random.random() * 0.5)


def claim_next(conn, now_iso):
    while True:
        run, job, skipped = _claim_one(conn, now_iso)
        if not skipped:
            return run, job


def _claim_one(conn, now_iso):
    row = conn.execute(
        """SELECT r.* FROM runs r JOIN jobs j ON j.job_id = r.job_id
           WHERE r.status IN ('pending', 'failed') AND j.enabled = 1
             AND r.scheduled_at_utc <= :now AND (r.next_retry_at IS NULL OR r.next_retry_at <= :now)
           ORDER BY COALESCE(r.next_retry_at, r.scheduled_at_utc), r.id LIMIT 1""",
        {"now": now_iso},
    ).fetchone()
    if row is None:
        return None, None, False
    job = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (row["job_id"],)).fetchone()

    lateness = (db.from_iso(now_iso) - db.from_iso(row["scheduled_at_utc"])).total_seconds()
    if row["attempts"] == 0 and job["max_lateness_seconds"] is not None and lateness > job["max_lateness_seconds"]:
        conn.execute("UPDATE runs SET status='skipped', finished_at=?, error=? WHERE id=? AND status=?",
                     (now_iso, "atrasado %ds > limite %ds" % (lateness, job["max_lateness_seconds"]),
                      row["id"], row["status"]))
        log.warning("run %d (%s) pulado: atraso %ds", row["id"], row["job_id"], lateness)
        return None, None, True

    lease = db.to_iso(db.from_epoch(time.time() + job["timeout_seconds"] + LEASE_GRACE_SECONDS))
    cur = conn.execute(
        "UPDATE runs SET status='running', attempts=attempts+1, started_at=?, lease_expires_at=?, "
        "next_retry_at=NULL WHERE id=? AND status=?",
        (now_iso, lease, row["id"], row["status"]),
    )
    if cur.rowcount != 1:
        return None, None, False
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (row["id"],)).fetchone()
    return run, job, False


class _Alarm(object):
    """Limite de duração via SIGALRM (worker é single-thread na execução)."""

    def __init__(self, seconds):
        self.seconds = seconds

    def _fire(self, signum, frame):
        raise tasks.TaskTimeout("tempo limite de %ds excedido" % self.seconds)

    def __enter__(self):
        self.prev = signal.signal(signal.SIGALRM, self._fire)
        signal.setitimer(signal.ITIMER_REAL, self.seconds)

    def __exit__(self, *exc):
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, self.prev)
        return False


def execute(conn, run, job):
    task = tasks.REGISTRY[job["kind"]]
    ctx = tasks.TaskContext(conn, run, job, json.loads(job["params_json"] or "{}"))
    log.info("run %d (%s @ %s) tentativa %d", run["id"], run["job_id"], run["scheduled_at_utc"], run["attempts"])
    error = None
    try:
        if task.transactional:
            conn.execute("BEGIN IMMEDIATE")
            try:
                with _Alarm(job["timeout_seconds"]):
                    result = task.func(ctx)
                _mark_success(conn, run, result)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        else:
            with _Alarm(job["timeout_seconds"]):
                result = task.func(ctx)
            _mark_success(conn, run, result)
        if task.network:
            db.set_state(conn, "network_last_ok_utc", db.to_iso(db.utcnow()))
        log.info("run %d concluído: %s", run["id"], tasks.sanitize(json.dumps(result)))
        return "succeeded"
    except tasks.TransientError as exc:
        error = exc
        if exc.network:
            db.set_state(conn, "network_last_error_utc", db.to_iso(db.utcnow()))
            db.set_state(conn, "network_last_error", tasks.sanitize(str(exc)))
    except tasks.TaskTimeout as exc:
        error = tasks.TransientError(str(exc))
    except tasks.PermanentError as exc:
        return _mark_dead(conn, run, "permanente: %s" % exc)
    except Exception as exc:  # erro inesperado da tarefa: tenta de novo dentro do limite
        error = tasks.TransientError("%s: %s" % (type(exc).__name__, exc))

    if run["attempts"] >= job["max_attempts"]:
        return _mark_dead(conn, run, "esgotou %d tentativas: %s" % (run["attempts"], error))
    delay = backoff_seconds(run["attempts"])
    if error.retry_after is not None:
        delay = max(delay, error.retry_after)
    next_at = db.to_iso(db.from_epoch(time.time() + delay))
    conn.execute(
        "UPDATE runs SET status='failed', next_retry_at=?, finished_at=?, lease_expires_at=NULL, error=? WHERE id=?",
        (next_at, db.to_iso(db.utcnow()), tasks.sanitize(str(error)), run["id"]),
    )
    log.warning("run %d falhou (tentativa %d/%d): %s; nova tentativa em %ds",
                run["id"], run["attempts"], job["max_attempts"], tasks.sanitize(str(error)), delay)
    return "failed"


def _mark_success(conn, run, result):
    conn.execute(
        "UPDATE runs SET status='succeeded', finished_at=?, lease_expires_at=NULL, error=NULL, result=? WHERE id=?",
        (db.to_iso(db.utcnow()), json.dumps(result, sort_keys=True), run["id"]),
    )


def _mark_dead(conn, run, message):
    conn.execute(
        "UPDATE runs SET status='dead', finished_at=?, lease_expires_at=NULL, error=? WHERE id=?",
        (db.to_iso(db.utcnow()), tasks.sanitize(message), run["id"]),
    )
    log.error("run %d (%s) FALHA PERMANENTE: %s", run["id"], run["job_id"], tasks.sanitize(message))
    return "dead"


def recover_interrupted(conn):
    """Chamado no início, com o lock exclusivo: todo 'running' é órfão de uma queda.

    Sem efeito externo -> volta à fila. Com efeito externo -> needs_review
    (resultado ambíguo; não reexecuta automaticamente).
    """
    now = db.to_iso(db.utcnow())
    rows = conn.execute(
        "SELECT r.id, r.job_id, r.attempts, j.external_effect, j.max_attempts FROM runs r "
        "JOIN jobs j ON j.job_id = r.job_id WHERE r.status = 'running'"
    ).fetchall()
    for r in rows:
        if r["external_effect"]:
            conn.execute("UPDATE runs SET status='needs_review', finished_at=?, lease_expires_at=NULL, error=? "
                         "WHERE id=?", (now, "interrompido durante execução; efeito externo ambíguo: conciliar", r["id"]))
            log.error("run %d (%s) interrompido com efeito externo: needs_review", r["id"], r["job_id"])
        elif r["attempts"] >= r["max_attempts"]:
            conn.execute("UPDATE runs SET status='dead', finished_at=?, lease_expires_at=NULL, error=? WHERE id=?",
                         (now, "interrompido na última tentativa", r["id"]))
            log.error("run %d (%s) interrompido na última tentativa: dead", r["id"], r["job_id"])
        else:
            conn.execute("UPDATE runs SET status='failed', next_retry_at=?, lease_expires_at=NULL, error=? "
                         "WHERE id=?", (now, "interrompido; reenfileirado (tarefa local/idempotente)", r["id"]))
            log.warning("run %d (%s) interrompido: reenfileirado", r["id"], r["job_id"])
    return len(rows)


# --- processo -------------------------------------------------------------------


class Worker(object):
    def __init__(self, db_path=None):
        self.db_path = db_path or db.default_db_path()
        self.stop_event = threading.Event()
        self.signals = 0
        self.progress_mono = time.monotonic()
        self.progress_limit = 180
        self.lock_fh = None

    def acquire_lock(self):
        run_dir = os.environ.get("E4T_RUN_DIR") or os.path.join(PROJECT_DIR, "run")
        os.makedirs(run_dir, mode=0o700, exist_ok=True)
        self.lock_fh = open(os.path.join(run_dir, "worker.lock"), "a+")
        try:
            fcntl.flock(self.lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_fh.close()
            self.lock_fh = None
            return False
        self.lock_fh.seek(0)
        self.lock_fh.truncate()
        self.lock_fh.write("%d\n" % os.getpid())
        self.lock_fh.flush()
        return True

    def _on_signal(self, signum, frame):
        self.signals += 1
        if self.signals == 1:
            log.info("sinal %d recebido: encerrando após a tarefa atual (checkpoint)", signum)
            self.stop_event.set()
        else:
            raise SystemExit(128 + signum)

    def _watchdog(self):
        """Se o laço principal travar (sem progresso), sai com código 70 para o supervisor reiniciar."""
        while not self.stop_event.wait(10):
            idle = time.monotonic() - self.progress_mono
            if idle > self.progress_limit:
                log.critical("watchdog: sem progresso há %ds; saindo para reinício controlado", idle)
                logging.shutdown()
                os._exit(EXIT_WATCHDOG)

    def heartbeat(self, conn):
        now = db.to_iso(db.utcnow())
        db.set_state(conn, "heartbeat_utc", now)
        db.set_state(conn, "heartbeat_pid", str(os.getpid()))

    def external_heartbeat(self, conn):
        url = os.environ.get("E4T_HEARTBEAT_URL", "").strip()
        if not env_flag("E4T_HEARTBEAT_ENABLED") or not url.startswith("https://"):
            return
        try:
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": "estiven4tech-mobile-server/1"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read(1024)
            db.set_state(conn, "external_heartbeat_ok_utc", db.to_iso(db.utcnow()))
        except Exception as exc:
            db.set_state(conn, "external_heartbeat_error", tasks.sanitize("%s: %s" % (type(exc).__name__, exc)))
            log.warning("heartbeat externo falhou: %s", tasks.sanitize(type(exc).__name__))

    def run(self):
        if not self.acquire_lock():
            log.error("outro worker já está rodando (lock em run/worker.lock); saindo")
            return EXIT_LOCKED
        conn = db.connect(self.db_path)
        version = db.migrate(conn)
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        previous_clean = db.get_state(conn, "clean_shutdown")
        log.info("worker pid %d iniciado; schema v%d; journal=%s; banco=%s",
                 os.getpid(), version, journal, os.path.relpath(self.db_path, PROJECT_DIR))
        if previous_clean == "0":
            log.warning("encerramento anterior não foi limpo (queda, kill ou Android); recuperando")
        db.set_state(conn, "clean_shutdown", "0")
        db.set_state(conn, "worker_started_utc", db.to_iso(db.utcnow()))
        db.set_state(conn, "journal_mode", journal)
        recovered = recover_interrupted(conn)
        if recovered:
            log.info("%d execução(ões) interrompida(s) tratada(s)", recovered)

        path = jobs_config_path()
        sync_jobs(conn, load_jobs(path))
        log.info("jobs carregados de %s", os.path.relpath(path, PROJECT_DIR))

        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        threading.Thread(target=self._watchdog, name="watchdog", daemon=True).start()

        tick = max(1, env_int("E4T_TICK_SECONDS", 5))
        hb_every = max(5, env_int("E4T_HEARTBEAT_SECONDS", 60))
        ext_every = max(60, env_int("E4T_HEARTBEAT_EXTERNAL_SECONDS", 300))
        last_hb = last_ext = -1e9
        last_wall, last_mono = time.time(), time.monotonic()

        try:
            while not self.stop_event.is_set():
                mono, wall = time.monotonic(), time.time()
                self.progress_mono, self.progress_limit = mono, 180
                drift = (wall - last_wall) - (mono - last_mono)
                if abs(drift) > 120:
                    log.warning("salto de relógio detectado: %+ds (monotônico vs UTC)", drift)
                    db.set_state(conn, "clock_jump_utc", db.to_iso(db.utcnow()))
                last_wall, last_mono = wall, mono

                if mono - last_hb >= hb_every:
                    self.heartbeat(conn)
                    last_hb = mono
                if mono - last_ext >= ext_every:
                    self.external_heartbeat(conn)
                    last_ext = mono

                schedule_due(conn, wall)
                run, job = claim_next(conn, db.to_iso(db.from_epoch(wall)))
                if run is not None:
                    self.progress_limit = job["timeout_seconds"] + 60
                    execute(conn, run, job)
                    continue
                self.stop_event.wait(tick)
        finally:
            self.stop_event.set()
            self.checkpoint(conn)
        return 0

    def checkpoint(self, conn):
        try:
            db.set_state(conn, "clean_shutdown", "1")
            db.set_state(conn, "last_shutdown_utc", db.to_iso(db.utcnow()))
            if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal":
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            log.info("checkpoint gravado; worker encerrado")
        except Exception as exc:
            log.error("falha no checkpoint: %s", tasks.sanitize(str(exc)))


class _SanitizingFilter(logging.Filter):
    def filter(self, record):
        record.msg = tasks.sanitize(record.getMessage(), limit=1000)
        record.args = ()
        return True


def setup_logging():
    handler = logging.StreamHandler(sys.stdout)  # supervisor grava e rotaciona (5 MB x 3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S%z"))
    handler.addFilter(_SanitizingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.environ.get("E4T_LOG_LEVEL", "INFO").upper())


def main():
    load_env_file()
    os.environ.setdefault("TZ", "America/Sao_Paulo")
    time.tzset()
    setup_logging()
    sys.exit(Worker().run())


if __name__ == "__main__":
    main()
