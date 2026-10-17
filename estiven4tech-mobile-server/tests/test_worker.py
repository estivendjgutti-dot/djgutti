"""Testes do worker, fila, retry, recuperação e backup. Somente stdlib; sem rede externa.

Rodar: .venv/bin/python -m unittest discover -s tests -v
"""

import http.server
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC)

import backup  # noqa: E402
import db  # noqa: E402
import tasks  # noqa: E402
import worker  # noqa: E402


class _Stub(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/ok":
            self._send(200)
        elif path == "/429":
            self._send(429, {"Retry-After": "120"})
        elif path == "/500":
            self._send(500)
        elif path == "/404":
            self._send(404)
        elif path == "/slow":
            time.sleep(3)
            self._send(200)

    def _send(self, code, headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", "2")
        self.end_headers()
        try:
            self.wfile.write(b"ok")
        except OSError:
            pass

    def log_message(self, *a):
        pass


def job_def(job_id, kind, interval=300, **kw):
    d = {"job_id": job_id, "kind": kind, "enabled": True, "interval_seconds": interval}
    d.update(kw)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="e4t-test-")
        os.environ["E4T_DB_PATH"] = os.path.join(self.tmp, "data", "app.db")
        os.environ["E4T_RUN_DIR"] = os.path.join(self.tmp, "run")
        os.environ["E4T_BACKUP_DIR"] = os.path.join(self.tmp, "backups")
        os.environ["E4T_ALLOW_SHORT_INTERVALS"] = "1"
        self.conn = db.connect()
        db.migrate(self.conn)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load(self, *defs):
        path = os.path.join(self.tmp, "jobs.json")
        with open(path, "w") as fh:
            json.dump({"jobs": list(defs)}, fh)
        worker.sync_jobs(self.conn, worker.load_jobs(path))

    def runs(self, job_id):
        return self.conn.execute("SELECT * FROM runs WHERE job_id=? ORDER BY scheduled_at_utc", (job_id,)).fetchall()

    def run_once(self, now=None):
        now = now or time.time()
        worker.schedule_due(self.conn, now)
        run, job = worker.claim_next(self.conn, db.to_iso(db.from_epoch(now)))
        if run is None:
            return None, None
        return worker.execute(self.conn, run, job), run["id"]

    def set_last_slot(self, job_id, ts):
        self.conn.execute("UPDATE jobs SET last_slot_utc=? WHERE job_id=?", (db.to_iso(db.from_epoch(ts)), job_id))


class SchedulingTests(Base):
    def test_unique_slot_no_duplicates(self):
        self.load(job_def("demo", "demo_timestamp"))
        now = 1_800_000_100
        for _ in range(3):
            worker.schedule_due(self.conn, now)
        self.assertEqual(len(self.runs("demo")), 1)
        # mesmo forçando reinício da agenda, o UNIQUE impede duplicar
        self.conn.execute("UPDATE jobs SET last_slot_utc=NULL")
        worker.schedule_due(self.conn, now)
        self.assertEqual(len(self.runs("demo")), 1)

    def test_catchup_latest_registers_gaps(self):
        self.load(job_def("demo", "demo_timestamp"))
        now = 1_800_000_000 + 10
        self.set_last_slot("demo", 1_800_000_000 - 10 * 300)
        worker.schedule_due(self.conn, now)
        statuses = [r["status"] for r in self.runs("demo")]
        self.assertEqual(statuses.count("pending"), 1)
        self.assertEqual(statuses.count("skipped"), 9)
        self.assertEqual(self.runs("demo")[-1]["status"], "pending")

    def test_catchup_skip_sends_nothing_old(self):
        self.load(job_def("msg", "demo_timestamp", catchup="skip", max_lateness_seconds=30))
        base = 1_800_000_000
        self.set_last_slot("msg", base - 5 * 300)
        worker.schedule_due(self.conn, base + 200)  # 200 s depois do slot atual > 30 s de tolerância
        self.assertEqual([r["status"] for r in self.runs("msg")].count("pending"), 0)

    def test_catchup_window(self):
        self.load(job_def("rep", "demo_timestamp", interval=60, catchup="window", catchup_window_seconds=180))
        base = 1_800_000_000
        self.set_last_slot("rep", base - 600)
        worker.schedule_due(self.conn, base + 1)
        pending = [r for r in self.runs("rep") if r["status"] == "pending"]
        self.assertEqual(len(pending), 3)  # idade <= 180 s em base+1: base-120, base-60, base

    def test_clock_jump_backwards_resets(self):
        self.load(job_def("demo", "demo_timestamp"))
        self.set_last_slot("demo", 1_900_000_000)
        worker.schedule_due(self.conn, 1_800_000_000)
        self.assertEqual(len(self.runs("demo")), 1)

    def test_max_lateness_skips_at_claim(self):
        self.load(job_def("msg", "demo_timestamp", max_lateness_seconds=60))
        worker.schedule_due(self.conn, 1_800_000_000)
        run, _ = worker.claim_next(self.conn, db.to_iso(db.from_epoch(1_800_000_000 + 600)))
        self.assertIsNone(run)
        self.assertEqual(self.runs("msg")[0]["status"], "skipped")

    def test_invalid_jobs_rejected(self):
        os.environ.pop("E4T_ALLOW_SHORT_INTERVALS")
        try:
            self.load(job_def("x", "demo_timestamp", interval=5), job_def("h", "http_check", params={"url": ""}),
                      job_def("t", "test_sleep"), job_def("k", "nao_existe"))
        finally:
            os.environ["E4T_ALLOW_SHORT_INTERVALS"] = "1"
        rows = {r["job_id"]: r["enabled"] for r in self.conn.execute("SELECT * FROM jobs")}
        self.assertEqual(rows, {"h": 0})


class ExecutionTests(Base):
    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        cls.base_url = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        super().setUp()
        for k in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
            os.environ.pop(k, None)
        os.environ["no_proxy"] = "127.0.0.1"

    def http_job(self, path, **kw):
        params = {"url": self.base_url + path, "http_timeout_seconds": 1}
        self.load(job_def("http", "http_check", params=params, timeout_seconds=10, **kw))

    def test_demo_persists_once(self):
        self.load(job_def("demo", "demo_timestamp"))
        now = time.time()
        self.assertEqual(self.run_once(now)[0], "succeeded")
        self.assertIsNone(self.run_once(now)[0])
        n = self.conn.execute("SELECT COUNT(*), COUNT(DISTINCT run_id) FROM demo_events").fetchone()
        self.assertEqual(tuple(n), (1, 1))

    def test_http_ok(self):
        self.http_job("/ok")
        self.assertEqual(self.run_once()[0], "succeeded")
        self.assertIsNotNone(db.get_state(self.conn, "network_last_ok_utc"))

    def test_http_429_respects_retry_after(self):
        self.http_job("/429")
        before = time.time()
        outcome, run_id = self.run_once()
        self.assertEqual(outcome, "failed")
        r = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        self.assertGreaterEqual(db.from_iso(r["next_retry_at"]).timestamp(), before + 119)
        self.assertIn("HTTP 429", r["error"])

    def test_http_500_backoff_then_dead_after_max_attempts(self):
        self.http_job("/500", max_attempts=5)
        before = time.time()
        outcome, run_id = self.run_once()
        r = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        delay = db.from_iso(r["next_retry_at"]).timestamp() - before
        self.assertTrue(13 <= delay <= 32, delay)  # 30 s * [0.5, 1.0]
        for _ in range(4):
            self.conn.execute("UPDATE runs SET next_retry_at=NULL WHERE id=?", (run_id,))
            run, job = worker.claim_next(self.conn, db.to_iso(db.utcnow()))
            outcome = worker.execute(self.conn, run, job)
        r = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        self.assertEqual((outcome, r["status"], r["attempts"]), ("dead", "dead", 5))
        self.assertIn("esgotou 5 tentativas", r["error"])

    def test_http_timeout_is_transient_network(self):
        self.http_job("/slow")
        outcome, run_id = self.run_once()
        self.assertEqual(outcome, "failed")
        self.assertIsNotNone(db.get_state(self.conn, "network_last_error_utc"))

    def test_http_404_is_permanent(self):
        self.http_job("/404")
        self.assertEqual(self.run_once()[0], "dead")

    def test_network_down(self):
        self.load(job_def("http", "http_check", params={"url": "http://127.0.0.1:9/x", "http_timeout_seconds": 1}))
        self.assertEqual(self.run_once()[0], "failed")
        self.assertIn("rede", db.get_state(self.conn, "network_last_error"))

    def test_task_duration_limit(self):
        self.load(job_def("lento", "test_sleep", timeout_seconds=1, params={"seconds": 3}))
        t0 = time.monotonic()
        outcome, run_id = self.run_once()
        self.assertLess(time.monotonic() - t0, 2.5)
        r = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        self.assertEqual(outcome, "failed")
        self.assertIn("tempo limite", r["error"])

    def test_error_and_log_sanitized(self):
        s = tasks.sanitize("falha em https://user:pw@api.x.com/v1?token=abc123 Authorization: Bearer abcdefghijk "
                           "api_key=XYZ senha=1234")
        for secret in ("pw@", "abc123", "abcdefghijk", "XYZ", "1234"):
            self.assertNotIn(secret, s)
        self.assertIn("api.x.com/v1?***", s)


class RecoveryTests(Base):
    def _orphan(self, job_id):
        worker.schedule_due(self.conn, time.time())
        run, _ = worker.claim_next(self.conn, db.to_iso(db.utcnow()))  # status running, nunca concluído
        self.assertEqual(run["job_id"], job_id)
        return run["id"]

    def test_local_task_requeued_and_runs_once(self):
        self.load(job_def("demo", "demo_timestamp"))
        run_id = self._orphan("demo")
        self.assertEqual(worker.recover_interrupted(self.conn), 1)
        self.assertEqual(self.conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()[0], "failed")
        self.assertEqual(self.run_once()[0], "succeeded")
        self.assertIsNone(self.run_once()[0])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM demo_events").fetchone()[0], 1)

    def test_external_effect_goes_to_review_not_rerun(self):
        self.load(job_def("envio", "test_sleep", external_effect=True, params={"seconds": 0}))
        run_id = self._orphan("envio")
        worker.recover_interrupted(self.conn)
        r = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        self.assertEqual((r["status"], r["attempts"]), ("needs_review", 1))
        self.assertIsNone(self.run_once()[0])


class ProcessTests(Base):
    def test_single_instance_lock(self):
        w1, w2 = worker.Worker(), worker.Worker()
        self.assertTrue(w1.acquire_lock())
        self.assertFalse(w2.acquire_lock())
        w1.lock_fh.close()
        self.assertTrue(w2.acquire_lock())
        w2.lock_fh.close()

    def test_sigterm_checkpoint(self):
        jobs = os.path.join(self.tmp, "jobs.json")
        with open(jobs, "w") as fh:
            json.dump({"jobs": [job_def("demo", "demo_timestamp", interval=2)]}, fh)
        env = dict(os.environ, E4T_JOBS_FILE=jobs, E4T_TICK_SECONDS="1")
        p = subprocess.Popen([sys.executable, os.path.join(SRC, "worker.py")], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        time.sleep(4)
        p.send_signal(signal.SIGTERM)
        out = p.communicate(timeout=30)[0].decode()
        self.assertEqual(p.returncode, 0, out)
        self.assertEqual(db.get_state(self.conn, "clean_shutdown"), "1")
        self.assertIn("checkpoint gravado", out)
        n = self.conn.execute("SELECT COUNT(*), COUNT(DISTINCT scheduled_at_utc) FROM demo_events").fetchone()
        self.assertGreaterEqual(n[0], 1)
        self.assertEqual(n[0], n[1])


class BackupTests(Base):
    def test_backup_verify_restore_to_temp(self):
        self.load(job_def("demo", "demo_timestamp"))
        self.run_once()
        path = backup.create_backup(reason="teste")
        res = backup.verify_archive(path)
        self.assertTrue(res["ok"], res["problems"])
        target = os.path.join(self.tmp, "restore-test")
        res = backup.verify_archive(path, dest=target)
        self.assertEqual(res["restored"]["counts"]["demo_events"], 1)
        self.assertEqual(res["restored"]["integrity"], "ok")
        self.assertFalse(os.path.exists(os.path.join(target, ".env")))
        self.assertEqual(len(os.listdir(os.path.join(self.tmp, "backups", "weekly"))), 1)

    def test_retention_keeps_newest_per_day(self):
        daily = os.path.join(self.tmp, "backups", "daily")
        weekly = os.path.join(self.tmp, "backups", "weekly")
        os.makedirs(daily)
        os.makedirs(weekly)
        for day in range(1, 11):
            for hh in ("01", "13"):
                open(os.path.join(daily, "e4t-202610%02dT%s0000Z.tar.gz" % (day, hh)), "w").close()
        for w in range(1, 7):
            open(os.path.join(weekly, "e4t-week-2026-W%02d-x.tar.gz" % w), "w").close()
        backup.apply_retention(7, 4)
        kept = sorted(os.listdir(daily))
        self.assertEqual(len(kept), 7)
        self.assertTrue(all("T13" in n for n in kept))
        self.assertEqual(kept[0], "e4t-20261004T130000Z.tar.gz")
        self.assertEqual(len(os.listdir(weekly)), 4)

    def test_production_restore_refuses_with_worker_running(self):
        self.load(job_def("demo", "demo_timestamp"))
        path = backup.create_backup()
        w = worker.Worker()
        self.assertTrue(w.acquire_lock())
        try:
            with self.assertRaises(SystemExit):
                backup.restore_production(path)
        finally:
            w.lock_fh.close()
        res = backup.restore_production(path)
        self.assertTrue(res["ok"])
        self.assertTrue(os.path.isdir(res["previous_saved_in"]))


if __name__ == "__main__":
    unittest.main()
