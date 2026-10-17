"""Backup consistente (API online do SQLite), retenção e restauração.

Uso:
  python src/backup.py create [--with-env]
  python src/backup.py verify ARQUIVO.tar.gz
  python src/backup.py restore ARQUIVO.tar.gz --target DIR          (teste, não toca produção)
  python src/backup.py restore ARQUIVO.tar.gz --production --yes    (worker precisa estar parado)
  python src/backup.py retention
"""

import argparse
import fcntl
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402

PROJECT_DIR = db.PROJECT_DIR
INCLUDE = ["AGENTS.md", "CLAUDE.md", "README.md", "requirements.lock", ".env.example", ".gitignore",
           "src", "config", "scripts", "docs", "tests"]
DB_ARCNAME = "data/app.db"


def backups_dir():
    return os.environ.get("E4T_BACKUP_DIR") or os.path.join(PROJECT_DIR, "backups")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect_db(path):
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {t: conn.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0] for t in tables}
    finally:
        conn.close()
    return {"integrity": integrity, "schema_version": version, "counts": counts}


def snapshot_db(src_path, dst_path):
    """Cópia online consistente, segura com WAL ativo e worker rodando."""
    src = sqlite3.connect(src_path, timeout=30)
    dst = sqlite3.connect(dst_path)
    try:
        src.execute("PRAGMA busy_timeout = 30000")
        src.backup(dst)
        dst.execute("PRAGMA journal_mode = DELETE")
    finally:
        dst.close()
        src.close()


def create_backup(reason="manual", include_env=False, db_path=None):
    db_path = db_path or db.default_db_path()
    if not os.path.exists(db_path):
        raise SystemExit("banco não encontrado: %s (inicie o worker ao menos uma vez)" % db_path)
    daily = os.path.join(backups_dir(), "daily")
    weekly = os.path.join(backups_dir(), "weekly")
    for d in (backups_dir(), daily, weekly):
        os.makedirs(d, mode=0o700, exist_ok=True)

    now = db.utcnow()
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    final = os.path.join(daily, "e4t-%s.tar.gz" % stamp)
    tmp = tempfile.mkdtemp(prefix=".tmp-", dir=backups_dir())
    try:
        snap = os.path.join(tmp, "app.db")
        snapshot_db(db_path, snap)
        info = inspect_db(snap)
        if info["integrity"] != "ok":
            raise SystemExit("integrity_check do snapshot falhou: %s" % info["integrity"])
        files = [p for p in INCLUDE if os.path.exists(os.path.join(PROJECT_DIR, p))]
        if include_env and os.path.exists(os.path.join(PROJECT_DIR, ".env")):
            files.append(".env")
        manifest = {
            "created_at_utc": db.to_iso(now),
            "reason": reason,
            "db": dict(info, sha256=_sha256(snap), arcname=DB_ARCNAME),
            "files": files,
            "includes_env": ".env" in files,
            "python": platform.python_version(),
            "machine": platform.machine(),
        }
        man_path = os.path.join(tmp, "manifest.json")
        with open(man_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)

        def _filter(ti):
            if "__pycache__" in ti.name or ti.name.endswith(".pyc"):
                return None
            return ti

        partial = final + ".partial"
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(man_path, arcname="manifest.json")
            tar.add(snap, arcname=DB_ARCNAME)
            for rel in files:
                tar.add(os.path.join(PROJECT_DIR, rel), arcname=rel, filter=_filter)
        os.chmod(partial, 0o600)
        os.replace(partial, final)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    year, week, _ = now.isocalendar()
    week_tag = "e4t-week-%04d-W%02d" % (year, week)
    if not any(n.startswith(week_tag) for n in os.listdir(weekly)):
        target = os.path.join(weekly, "%s-%s.tar.gz" % (week_tag, stamp))
        try:
            os.link(final, target)
        except OSError:
            shutil.copy2(final, target)
    return final


def apply_retention(daily_keep=None, weekly_keep=None):
    """Mantém o backup mais recente de cada um dos últimos N dias e as últimas M semanas."""
    daily_keep = daily_keep or int(os.environ.get("E4T_BACKUP_DAILY_KEEP", 7))
    weekly_keep = weekly_keep or int(os.environ.get("E4T_BACKUP_WEEKLY_KEEP", 4))
    removed = []
    daily = os.path.join(backups_dir(), "daily")
    if os.path.isdir(daily):
        names = sorted((n for n in os.listdir(daily) if n.endswith(".tar.gz")), reverse=True)
        keep_days, keep = [], set()
        for n in names:
            day = n[4:12]
            if day not in keep_days:
                if len(keep_days) >= daily_keep:
                    continue
                keep_days.append(day)
                keep.add(n)
        for n in names:
            if n not in keep:
                os.remove(os.path.join(daily, n))
                removed.append(n)
    weekly = os.path.join(backups_dir(), "weekly")
    if os.path.isdir(weekly):
        names = sorted((n for n in os.listdir(weekly) if n.endswith(".tar.gz")), reverse=True)
        for n in names[weekly_keep:]:
            os.remove(os.path.join(weekly, n))
            removed.append(n)
    return removed


def _safe_extract(tar, dest):
    dest_abs = os.path.realpath(dest)
    for member in tar.getmembers():
        target = os.path.realpath(os.path.join(dest, member.name))
        if not (target == dest_abs or target.startswith(dest_abs + os.sep)) or member.issym() or member.islnk():
            raise SystemExit("arquivo inseguro no backup: %s" % member.name)
    if hasattr(tarfile, "data_filter"):
        tar.extractall(dest, filter="data")
    else:
        tar.extractall(dest)


def verify_archive(archive, dest=None):
    """Extrai (em temp ou dest), confere sha256, integridade e contagens do manifesto."""
    own_tmp = dest is None
    dest = dest or tempfile.mkdtemp(prefix="e4t-verify-")
    try:
        with tarfile.open(archive, "r:gz") as tar:
            _safe_extract(tar, dest)
        with open(os.path.join(dest, "manifest.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)
        db_file = os.path.join(dest, DB_ARCNAME)
        info = inspect_db(db_file)
        problems = []
        if _sha256(db_file) != manifest["db"]["sha256"]:
            problems.append("sha256 diferente do manifesto")
        if info["integrity"] != "ok":
            problems.append("integrity_check: %s" % info["integrity"])
        if info["counts"] != manifest["db"]["counts"]:
            problems.append("contagens diferentes do manifesto")
        return {"ok": not problems, "problems": problems, "manifest": manifest, "restored": info, "dir": dest}
    finally:
        if own_tmp:
            shutil.rmtree(dest, ignore_errors=True)


def worker_running():
    lock_path = os.path.join(os.environ.get("E4T_RUN_DIR") or os.path.join(PROJECT_DIR, "run"), "worker.lock")
    if not os.path.exists(lock_path):
        return False
    with open(lock_path, "a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
    return False


def restore_production(archive, with_config=False):
    if worker_running():
        raise SystemExit("worker em execução: rode scripts/stop.sh antes de restaurar")
    tmp = tempfile.mkdtemp(prefix=".restore-", dir=backups_dir())
    try:
        result = verify_archive(archive, dest=tmp)
        if not result["ok"]:
            raise SystemExit("backup reprovado na verificação: %s" % "; ".join(result["problems"]))
        db_path = db.default_db_path()
        data_dir = os.path.dirname(db_path)
        os.makedirs(data_dir, mode=0o700, exist_ok=True)
        stamp = db.utcnow().strftime("%Y%m%dT%H%M%SZ")
        keep_dir = os.path.join(data_dir, "pre-restore-%s" % stamp)
        moved = []
        for suffix in ("", "-wal", "-shm", "-journal"):
            if os.path.exists(db_path + suffix):
                os.makedirs(keep_dir, mode=0o700, exist_ok=True)
                shutil.move(db_path + suffix, os.path.join(keep_dir, os.path.basename(db_path) + suffix))
                moved.append(suffix or "db")
        shutil.copy2(os.path.join(tmp, DB_ARCNAME), db_path)
        os.chmod(db_path, 0o600)
        if with_config:
            src_jobs = os.path.join(tmp, "config", "jobs.json")
            if os.path.exists(src_jobs):
                dst_jobs = os.path.join(PROJECT_DIR, "config", "jobs.json")
                if os.path.exists(dst_jobs):
                    shutil.copy2(dst_jobs, dst_jobs + ".pre-restore-%s" % stamp)
                shutil.copy2(src_jobs, dst_jobs)
        result["previous_saved_in"] = keep_dir if moved else None
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Backup/restauração do estiven4tech-mobile-server")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--with-env", action="store_true", help="inclui .env (contém segredos; evite)")
    c.add_argument("--reason", default="manual")
    v = sub.add_parser("verify")
    v.add_argument("archive")
    r = sub.add_parser("restore")
    r.add_argument("archive")
    r.add_argument("--target", help="pasta vazia para teste de restauração (não toca produção)")
    r.add_argument("--production", action="store_true")
    r.add_argument("--with-config", action="store_true", help="também restaura config/jobs.json")
    r.add_argument("--yes", action="store_true")
    sub.add_parser("retention")
    args = parser.parse_args(argv)

    if args.cmd == "create":
        path = create_backup(reason=args.reason, include_env=args.with_env)
        removed = apply_retention()
        res = verify_archive(path)
        print("backup: %s" % os.path.relpath(path, PROJECT_DIR))
        print("verificação: %s %s" % ("OK" if res["ok"] else "FALHOU", "; ".join(res["problems"])))
        print("contagens: %s" % json.dumps(res["restored"]["counts"], sort_keys=True))
        if removed:
            print("retenção removeu: %s" % ", ".join(removed))
        return 0 if res["ok"] else 1
    if args.cmd == "verify":
        res = verify_archive(args.archive)
        print(json.dumps({k: res[k] for k in ("ok", "problems", "restored")}, indent=2, sort_keys=True))
        return 0 if res["ok"] else 1
    if args.cmd == "retention":
        print("\n".join(apply_retention()) or "nada a remover")
        return 0
    if args.cmd == "restore":
        if args.target:
            if os.path.exists(args.target) and os.listdir(args.target):
                raise SystemExit("--target precisa ser uma pasta nova ou vazia")
            os.makedirs(args.target, mode=0o700, exist_ok=True)
            res = verify_archive(args.archive, dest=args.target)
            print("restaurado em %s" % args.target)
            print(json.dumps({k: res[k] for k in ("ok", "problems", "restored")}, indent=2, sort_keys=True))
            return 0 if res["ok"] else 1
        if not args.production:
            raise SystemExit("informe --target DIR (teste) ou --production (substitui o banco)")
        if not args.yes:
            raise SystemExit("restauração de produção exige --yes")
        res = restore_production(args.archive, with_config=args.with_config)
        print("banco restaurado; anterior guardado em %s" % res["previous_saved_in"])
        print("contagens: %s" % json.dumps(res["restored"]["counts"], sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
