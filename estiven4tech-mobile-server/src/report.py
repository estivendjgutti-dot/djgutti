"""Relatório para os testes longos (tela apagada, 8h, 24h, Wi-Fi): agenda x execução.

Uso: python src/report.py [--horas 24] [--job demo_timestamp]
Mostra slots esperados, executados, pulados, atraso de início (p50/p95/máx),
maiores lacunas entre execuções, RSS atual dos processos e espaço em disco.
"""

import argparse
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402
import status  # noqa: E402


def pct(values, p):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100.0 * (len(values) - 1))))]


def rss_mb():
    total = 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as fh:
                cmd = fh.read().decode("utf-8", "replace")
            if db.PROJECT_DIR not in cmd or ".venv/bin/" not in cmd:
                continue
            with open("/proc/%s/status" % pid) as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        total += int(line.split()[1])
        except OSError:
            continue
    return total / 1024.0


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--horas", type=float, default=24)
    p.add_argument("--job", default="demo_timestamp")
    args = p.parse_args(argv)
    os.environ.setdefault("TZ", "America/Sao_Paulo")
    time.tzset()

    conn = db.connect(readonly=True)
    job = conn.execute("SELECT * FROM jobs WHERE job_id=?", (args.job,)).fetchone()
    if job is None:
        print("job %s não encontrado" % args.job)
        return 1
    now = db.utcnow()
    since = now.timestamp() - args.horas * 3600
    since_iso = db.to_iso(db.from_epoch(since))
    rows = conn.execute("SELECT * FROM runs WHERE job_id=? AND scheduled_at_utc >= ? ORDER BY scheduled_at_utc",
                        (args.job, since_iso)).fetchall()
    first = rows[0]["scheduled_at_utc"] if rows else None
    span = now.timestamp() - (db.from_iso(first).timestamp() if first else now.timestamp())
    expected = int(span // job["interval_seconds"]) + (1 if rows else 0)
    by = {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
    delays = [(db.from_iso(r["started_at"]) - db.from_iso(r["scheduled_at_utc"])).total_seconds()
              for r in rows if r["status"] == "succeeded" and r["started_at"]]
    ok_times = [db.from_iso(r["started_at"]).timestamp() for r in rows if r["status"] == "succeeded" and r["started_at"]]
    gaps = sorted(((b - a, a) for a, b in zip(ok_times, ok_times[1:])), reverse=True)[:5]

    print("== Relatório %s, últimas %.1fh (desde %s)" % (args.job, args.horas, status.local(since_iso)))
    print("intervalo: %ds | slots esperados desde o primeiro registro: %d" % (job["interval_seconds"], expected))
    print("execuções: " + (", ".join("%s=%d" % kv for kv in sorted(by.items())) or "nenhuma"))
    if delays:
        print("atraso de início: p50=%.0fs p95=%.0fs máx=%.0fs (meta piloto: < 60 s)" %
              (pct(delays, 50), pct(delays, 95), max(delays)))
        late = sum(1 for d in delays if d >= 60)
        print("execuções com atraso >= 60 s: %d de %d" % (late, len(delays)))
    for gap, start in gaps:
        if gap > job["interval_seconds"] * 1.5:
            print("lacuna: %.1f min após %s" % (gap / 60.0, status.local(db.to_iso(db.from_epoch(start)))))
    dup = conn.execute("SELECT COUNT(*) - COUNT(DISTINCT scheduled_at_utc) FROM demo_events").fetchone()[0]
    print("efeitos duplicados na demo: %d" % dup)
    print("RSS atual (supervisord+worker+listener): %.1f MB (meta < 500 MB)" % rss_mb())
    sizes = {d: status.dir_size(os.path.join(db.PROJECT_DIR, d)) / 1e6 for d in ("data", "logs", "backups")}
    print("disco: data=%.1f MB logs=%.1f MB backups=%.1f MB" % (sizes["data"], sizes["logs"], sizes["backups"]))
    try:
        print("carga (1/5/15 min): %s" % " ".join(open("/proc/loadavg").read().split()[:3]))
    except OSError:
        print("carga: /proc/loadavg indisponível")
    conn.close()
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # ex.: status.sh | head
    sys.exit(main())
