"""Relatório de saúde lido do SQLite (somente leitura).

Diferencia: processo vivo (estado do supervisor, passado por argumento),
worker travado (heartbeat antigo / run com lease vencida), falhas permanentes
e ausência de rede (estado gravado pelas tarefas HTTP; --rede faz um teste de DNS).
Saída 0 = saudável, 1 = atenção, 2 = parado/sem dados.
"""

import argparse
import datetime as _dt
import json
import os
import signal
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402

STALE_SECONDS = int(os.environ.get("E4T_STALE_SECONDS", 180))


def local(iso):
    dt = db.from_iso(iso)
    return dt.astimezone().strftime("%d/%m %H:%M:%S") if dt else "-"


def age(iso, now):
    dt = db.from_iso(iso)
    if not dt:
        return None
    return int((now - dt).total_seconds())


def fmt_age(seconds):
    if seconds is None:
        return "nunca"
    if seconds < 120:
        return "há %ds" % seconds
    if seconds < 7200:
        return "há %dmin" % (seconds // 60)
    return "há %.1fh" % (seconds / 3600.0)


def dns_probe(host, timeout=5):
    result = {}

    def _go():
        try:
            socket.getaddrinfo(host, 443)
            result["ok"] = True
        except OSError as exc:
            result["err"] = type(exc).__name__

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    t.join(timeout)
    if result.get("ok"):
        return "DNS OK (%s)" % host
    return "DNS FALHOU (%s: %s)" % (host, result.get("err", "timeout"))


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--worker-state", default="DESCONHECIDO", help="estado do programa no supervisor")
    p.add_argument("--rede", action="store_true", help="testa resolução DNS dos hosts configurados")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    os.environ.setdefault("TZ", "America/Sao_Paulo")
    time.tzset()

    path = db.default_db_path()
    if not os.path.exists(path):
        print("banco ainda não existe (%s): worker nunca iniciou" % os.path.relpath(path, db.PROJECT_DIR))
        return 2
    conn = db.connect(path, readonly=True)
    now = db.utcnow()
    state = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM state")}
    issues, warnings = [], []

    hb_age = age(state.get("heartbeat_utc"), now)
    proc_alive = args.worker_state in ("RUNNING", "STARTING")
    if not proc_alive:
        issues.append("worker não está RUNNING no supervisor (%s)" % args.worker_state)
    elif args.worker_state == "STARTING":
        warnings.append("worker iniciando (reinício recente)")
    if hb_age is None or hb_age > STALE_SECONDS:
        msg = "heartbeat local antigo (%s)" % fmt_age(hb_age)
        if proc_alive:
            issues.append(msg + ": processo vivo mas possivelmente travado")
        else:
            warnings.append(msg)

    now_iso = db.to_iso(now)
    stuck = conn.execute("SELECT id, job_id FROM runs WHERE status='running' AND lease_expires_at < ?",
                         (now_iso,)).fetchall()
    if stuck and proc_alive:
        issues.append("run(s) com lease vencida: %s" % ", ".join("%d/%s" % (r[0], r[1]) for r in stuck))

    counts = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM runs GROUP BY status")}
    day_ago = db.to_iso(now - _dt.timedelta(hours=24))
    recent_bad = {r[0]: r[1] for r in conn.execute(
        "SELECT status, COUNT(*) FROM runs WHERE status IN ('dead','needs_review','skipped') "
        "AND COALESCE(finished_at, created_at_utc) >= ? GROUP BY status", (day_ago,))}
    if counts.get("needs_review"):
        issues.append("%d execução(ões) needs_review: conciliar manualmente" % counts["needs_review"])
    if recent_bad.get("dead"):
        warnings.append("%d falha(s) permanente(s) nas últimas 24h" % recent_bad["dead"])
    if recent_bad.get("skipped"):
        warnings.append("%d slot(s) pulado(s) nas últimas 24h (worker parado/atrasado)" % recent_bad["skipped"])

    jobs = []
    for j in conn.execute("SELECT * FROM jobs ORDER BY job_id"):
        last_ok = conn.execute("SELECT MAX(finished_at) FROM runs WHERE job_id=? AND status='succeeded'",
                               (j["job_id"],)).fetchone()[0]
        last = conn.execute("SELECT status, scheduled_at_utc, error FROM runs WHERE job_id=? AND status != 'skipped' "
                            "ORDER BY scheduled_at_utc DESC, id DESC LIMIT 1", (j["job_id"],)).fetchone()
        ok_age = age(last_ok, now)
        if j["enabled"] and proc_alive and (ok_age is None or ok_age > 2 * j["interval_seconds"] + 120):
            if last is not None:
                warnings.append("job %s sem sucesso recente (%s)" % (j["job_id"], fmt_age(ok_age)))
        jobs.append({
            "job_id": j["job_id"], "kind": j["kind"], "enabled": bool(j["enabled"]),
            "interval_s": j["interval_seconds"], "last_success": last_ok, "last_success_age_s": ok_age,
            "last_status": last["status"] if last else None, "last_error": last["error"] if last else None,
        })

    net_ok, net_err = state.get("network_last_ok_utc"), state.get("network_last_error_utc")
    if net_err and (not net_ok or net_err > net_ok):
        warnings.append("rede: última verificação HTTP falhou %s (%s)" %
                        (fmt_age(age(net_err, now)), state.get("network_last_error", "?")))

    proj = db.PROJECT_DIR
    sizes = {d: dir_size(os.path.join(proj, d)) for d in ("data", "logs", "backups")}
    probe = None
    if args.rede:
        hosts = set()
        for j in conn.execute("SELECT params_json FROM jobs WHERE enabled=1 AND kind='http_check'"):
            url = json.loads(j[0]).get("url", "")
            if "://" in url:
                hosts.add(url.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0])
        probe = [dns_probe(h) for h in sorted(hosts)] or ["nenhum host http_check ativo para testar"]
    conn.close()

    if not proc_alive:
        verdict = 2
    else:
        verdict = 1 if issues or warnings else 0
    label = {0: "SAUDÁVEL", 1: "ATENÇÃO", 2: "PARADO"}[verdict]

    if args.json:
        print(json.dumps({"verdict": label, "issues": issues, "warnings": warnings, "jobs": jobs,
                          "queue": counts, "state": state, "sizes": sizes, "dns": probe}, indent=2, sort_keys=True))
        return verdict

    print("== Saúde: %s" % label)
    print("heartbeat local: %s (%s) pid=%s" % (local(state.get("heartbeat_utc")), fmt_age(hb_age),
                                                state.get("heartbeat_pid", "-")))
    print("worker iniciado: %s | último encerramento limpo: %s | journal=%s" % (
        local(state.get("worker_started_utc")), local(state.get("last_shutdown_utc")),
        state.get("journal_mode", "?")))
    print("fila: " + (", ".join("%s=%d" % kv for kv in sorted(counts.items())) or "vazia"))
    print("rede: último OK %s | último erro %s" % (fmt_age(age(net_ok, now)), fmt_age(age(net_err, now))))
    if state.get("external_heartbeat_ok_utc") or state.get("external_heartbeat_error"):
        print("heartbeat externo: último OK %s" % fmt_age(age(state.get("external_heartbeat_ok_utc"), now)))
    else:
        print("heartbeat externo: desativado")
    for j in jobs:
        print("job %-16s %-14s %s a cada %ss | último sucesso %s (%s) | último status %s%s" % (
            j["job_id"], j["kind"], "ATIVO " if j["enabled"] else "inativo", j["interval_s"],
            local(j["last_success"]), fmt_age(j["last_success_age_s"]), j["last_status"] or "-",
            (" | erro: %s" % j["last_error"]) if j["last_error"] and j["last_status"] in ("failed", "dead") else ""))
    print("espaço: data=%.1f MB logs=%.1f MB backups=%.1f MB" % tuple(sizes[d] / 1e6 for d in ("data", "logs", "backups")))
    for line in probe or []:
        print("rede (teste): %s" % line)
    for i in issues:
        print("PROBLEMA: %s" % i)
    for w in warnings:
        print("aviso: %s" % w)
    return verdict


if __name__ == "__main__":
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # ex.: status.sh | head
    sys.exit(main())
