"""Eventlistener do supervisor: registra saídas inesperadas e entrada em FATAL.

Protocolo: stdout é o canal com o supervisor; o registro vai para stderr
(gravado pelo supervisor em logs/events.log, com rotação).
Não reinicia nada sozinho: FATAL exige ação (scripts/start.sh ou investigação).
"""

import datetime as _dt
import sys


def log(msg):
    now = _dt.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    sys.stderr.write("%s %s\n" % (now, msg))
    sys.stderr.flush()


def parse(line):
    return dict(tok.split(":", 1) for tok in line.split() if ":" in tok)


def main():
    while True:
        sys.stdout.write("READY\n")
        sys.stdout.flush()
        header_line = sys.stdin.readline()
        if not header_line:
            return
        header = parse(header_line)
        payload = sys.stdin.read(int(header.get("len", 0)))
        body_line = payload.split("\n", 1)[0]
        body = parse(body_line)
        event = header.get("eventname", "?")
        name = body.get("processname", "?")
        if event == "PROCESS_STATE_FATAL":
            log("FATAL: programa %s esgotou as tentativas de início; rode scripts/status.sh e logs" % name)
        elif event == "PROCESS_STATE_EXITED":
            expected = body.get("expected", "?")
            if expected == "0":
                log("saída inesperada: programa %s (pid %s); supervisor tentará reiniciar" % (name, body.get("pid", "?")))
        elif event == "PROCESS_STATE_BACKOFF":
            log("BACKOFF: programa %s falhou ao iniciar; nova tentativa" % name)
        sys.stdout.write("RESULT 2\nOK")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
