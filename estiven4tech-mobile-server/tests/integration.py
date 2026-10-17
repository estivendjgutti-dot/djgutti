"""Validação de integração em sandbox: copia o projeto para uma pasta temporária,
usa sessão tmux própria (e4t-validacao) e exercita os scripts reais.

Cobre AGENTS.md §10: 1 (start duplo), 2 (demo persistida), 3 (kill do worker),
5 (queda durante efeito externo), 6 (backup/restauração), 10 (RSS), além de
heartbeat antigo, FATAL limitado e stop idempotente. Não usa rede externa.
"""

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEEP = "--keep" in sys.argv
results = []


def check(name, ok, evidence=""):
    results.append((name, bool(ok), evidence))
    print("[%s] %s%s" % ("OK" if ok else "FALHOU", name, (" | " + evidence) if evidence else ""), flush=True)
    return ok


def wait_for(fn, timeout, step=0.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            value = fn()
        except Exception:
            value = None
        if value:
            return value
        time.sleep(step)
    return None


class Sandbox(object):
    def __init__(self):
        base = os.environ.get("TMPDIR") or "/tmp"
        self.dir = tempfile.mkdtemp(prefix="e4tv-", dir=base)
        ignore = shutil.ignore_patterns(".venv", "data", "logs", "run", "backups", ".env", ".git", "__pycache__",
                                        "jobs.json", ".diag.*")
        for name in os.listdir(PROJECT):
            src = os.path.join(PROJECT, name)
            if name in (".venv", "data", "logs", "run", "backups", ".env", ".git"):
                continue
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(self.dir, name), ignore=ignore)
            else:
                shutil.copy2(src, self.dir)
        os.symlink(os.path.join(PROJECT, ".venv"), os.path.join(self.dir, ".venv"))
        with open(os.path.join(self.dir, ".env"), "w") as fh:
            fh.write("E4T_ALLOW_SHORT_INTERVALS=1\nE4T_TICK_SECONDS=1\nE4T_HEARTBEAT_SECONDS=5\n")
            # via .env: variáveis do shell não atravessam um servidor tmux já existente
            fh.write("E4T_SQLITE_JOURNAL=%s\n" % os.environ.get("E4T_SQLITE_JOURNAL", "wal"))
        self.write_jobs([
            {"job_id": "demo", "kind": "demo_timestamp", "enabled": True, "interval_seconds": 10,
             "timeout_seconds": 10},
            {"job_id": "envio_simulado", "kind": "test_sleep", "enabled": True, "interval_seconds": 3600,
             "timeout_seconds": 40, "external_effect": True, "params": {"seconds": 20}},
        ])
        self.env = dict(os.environ, E4T_TMUX_SESSION="e4t-validacao", E4T_STALE_SECONDS="15")
        for k in list(self.env):
            if k.startswith("E4T_") and k not in ("E4T_TMUX_SESSION", "E4T_STALE_SECONDS", "E4T_SQLITE_JOURNAL"):
                del self.env[k]

    def write_jobs(self, jobs):
        with open(os.path.join(self.dir, "config", "jobs.json"), "w") as fh:
            json.dump({"jobs": jobs}, fh)

    def sh(self, script, *args, **kw):
        p = subprocess.run([os.path.join(self.dir, "scripts", script)] + list(args), env=self.env, cwd="/",
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=kw.get("timeout", 240),
                           input=kw.get("input"), universal_newlines=True)
        return p.returncode, p.stdout

    def q(self, sql):
        c = sqlite3.connect("file:%s?mode=ro" % os.path.join(self.dir, "data", "app.db"), uri=True, timeout=10)
        try:
            return c.execute(sql).fetchall()
        finally:
            c.close()

    def pids(self, needle):
        found = []
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open("/proc/%s/cmdline" % pid, "rb") as fh:
                    cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
            except OSError:
                continue
            if self.dir in cmd and needle in cmd and "tmux" not in cmd.split(" ")[0]:
                found.append(int(pid))
        return sorted(found)

    def worker_pid(self):
        p = self.pids("src/worker.py")
        return p[0] if len(p) == 1 else None

    def rss_kb(self):
        total = 0
        for pid in self.pids(".venv/bin/"):
            try:
                with open("/proc/%d/status" % pid) as fh:
                    for line in fh:
                        if line.startswith("VmRSS:"):
                            total += int(line.split()[1])
            except OSError:
                pass
        return total


def main():
    sb = Sandbox()
    print("sandbox: %s" % sb.dir, flush=True)
    try:
        # 1) start duas vezes
        rc1, out1 = sb.sh("start.sh")
        rc2, out2 = sb.sh("start.sh")
        sup, wk = sb.pids("supervisord -n"), sb.pids("src/worker.py")
        check("1. start duplo: um supervisord e um worker", rc1 == 0 and rc2 == 0 and len(sup) == 1 and len(wk) == 1
              and "Nada a fazer" in out2, "supervisord=%s worker=%s" % (sup, wk))

        # 5) queda durante tarefa com efeito externo (envio simulado de 20 s)
        running = wait_for(lambda: sb.q("SELECT id FROM runs WHERE job_id='envio_simulado' AND status='running'"), 20)
        pid = sb.worker_pid()
        if running and pid:
            os.kill(pid, signal.SIGKILL)
        new_pid = wait_for(lambda: sb.worker_pid() if sb.worker_pid() not in (None, pid) else None, 30)
        review = wait_for(lambda: sb.q("SELECT status, attempts FROM runs WHERE job_id='envio_simulado'")
                          if sb.q("SELECT status FROM runs WHERE job_id='envio_simulado'")[0][0] == "needs_review"
                          else None, 20)
        time.sleep(3)
        after = sb.q("SELECT status, attempts FROM runs WHERE job_id='envio_simulado'")
        check("5. queda com efeito externo -> needs_review, sem reexecução", review and after == [("needs_review", 1)],
              "antes kill pid=%s, novo pid=%s, run=%s" % (pid, new_pid, after))
        rc, out = sb.sh("status.sh")
        check("   status.sh sinaliza conciliação pendente", rc == 1 and "needs_review" in out, "rc=%d" % rc)

        # 2) demo persistida
        ok = wait_for(lambda: sb.q("SELECT COUNT(*) FROM demo_events")[0][0] >= 2, 40)
        rows = sb.q("SELECT COUNT(*), COUNT(DISTINCT run_id), COUNT(DISTINCT scheduled_at_utc) FROM demo_events")[0]
        check("2. tarefa demo persistida no SQLite", ok and rows[0] == rows[1] == rows[2], "eventos/runs/slots=%s" % (rows,))

        # 3) encerrar só o worker
        pid = sb.worker_pid()
        before = sb.q("SELECT COUNT(*) FROM demo_events")[0][0]
        os.kill(pid, signal.SIGKILL)
        new_pid = wait_for(lambda: sb.worker_pid() if sb.worker_pid() not in (None, pid) else None, 30)
        wait_for(lambda: sb.q("SELECT COUNT(*) FROM demo_events")[0][0] > before, 30)
        rows = sb.q("SELECT COUNT(*), COUNT(DISTINCT scheduled_at_utc) FROM demo_events")[0]
        sup = sb.pids("supervisord -n")
        check("3. kill -9 no worker: supervisor reinicia, sem duplicar", new_pid and rows[0] == rows[1] and len(sup) == 1,
              "pid %s -> %s; eventos=%d slots distintos=%d" % (pid, new_pid, rows[0], rows[1]))
        with open(os.path.join(sb.dir, "logs", "events.log")) as fh:
            check("   events.log registra saída inesperada", "saída inesperada" in fh.read())

        # heartbeat antigo com processo vivo (SIGSTOP congela o worker inteiro)
        pid = sb.worker_pid()
        os.kill(pid, signal.SIGSTOP)
        time.sleep(22)
        rc, out = sb.sh("status.sh")
        os.kill(pid, signal.SIGCONT)
        check("   processo vivo mas travado detectado pelo heartbeat", rc == 1 and "possivelmente travado" in out,
              "rc=%d" % rc)
        wait_for(lambda: sb.sh("status.sh")[1].count("travado") == 0, 20)

        # 10) RSS
        rss = sb.rss_kb()
        check("10. RSS supervisord+worker+listener < 500 MB", 0 < rss < 500 * 1024, "%.1f MB" % (rss / 1024.0))

        # 6) backup online com worker rodando + restauração em pasta temporária
        rc, out = sb.sh("backup.sh")
        archive = sorted(os.listdir(os.path.join(sb.dir, "backups", "daily")))[-1]
        archive = os.path.join(sb.dir, "backups", "daily", archive)
        target = os.path.join(sb.dir, "restore-test")
        rc2, out2 = sb.sh("restore.sh", archive, "--target", target)
        res = json.loads(out2.split("\n", 1)[1])
        man = json.load(open(os.path.join(target, "manifest.json")))
        check("6. backup online + restauração em pasta temporária", rc == 0 and rc2 == 0 and res["ok"]
              and res["restored"]["integrity"] == "ok" and res["restored"]["counts"] == man["db"]["counts"],
              "contagens=%s" % json.dumps(res["restored"]["counts"], sort_keys=True))
        check("   backup não contém .env nem .venv", not os.path.exists(os.path.join(target, ".env"))
              and not os.path.exists(os.path.join(target, ".venv")))
        rc, out = sb.sh("restore.sh", archive, "--production", input="RESTAURAR\n")
        check("   restauração de produção recusada com servidor ativo", rc != 0 and "stop.sh" in out)

        # stop idempotente com checkpoint
        rc1, out1 = sb.sh("stop.sh")
        rc2, out2 = sb.sh("stop.sh")
        clean = sb.q("SELECT value FROM state WHERE key='clean_shutdown'")
        check("   stop duplo idempotente, checkpoint gravado", rc1 == 0 and rc2 == 0 and "já estava parado" in out2
              and not sb.pids(".venv/bin/") and clean == [("1",)], "clean_shutdown=%s" % clean)

        # restauração de produção com servidor parado
        before = sb.q("SELECT COUNT(*) FROM demo_events")[0][0]
        rc, out = sb.sh("restore.sh", archive, "--production", input="RESTAURAR\n")
        check("   restauração de produção com servidor parado", rc == 0 and "pre-restore" in out,
              "eventos antes=%d, depois=%d" % (before, sb.q("SELECT COUNT(*) FROM demo_events")[0][0]))

        # FATAL limitado: configuração quebrada não gera loop infinito
        with open(os.path.join(sb.dir, "config", "jobs.json"), "w") as fh:
            fh.write("{ quebrado")
        rc, out = sb.sh("start.sh", timeout=120)
        fatal = wait_for(lambda: "FATAL" in subprocess.run(
            [os.path.join(sb.dir, "scripts", "ctl.sh"), "status", "worker"], env=sb.env,
            stdout=subprocess.PIPE, universal_newlines=True).stdout, 60)
        with open(os.path.join(sb.dir, "logs", "events.log")) as fh:
            ev = fh.read()
        check("   falha contínua termina em FATAL (sem loop) e é registrada", fatal and "FATAL" in ev)
        sb.sh("stop.sh")
    finally:
        sb.sh("stop.sh")
        if KEEP or not all(ok for _, ok, _ in results):
            print("sandbox mantido para inspeção: %s" % sb.dir)
        else:
            shutil.rmtree(sb.dir, ignore_errors=True)

    failed = [n for n, ok, _ in results if not ok]
    print("\nresumo: %d OK, %d falha(s)" % (len(results) - len(failed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
