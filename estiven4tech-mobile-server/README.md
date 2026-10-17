# estiven4tech-mobile-server

Servidor leve de automações pessoais para o **Galaxy S10 Plus com UserLAnd** (Debian/Ubuntu, sem desktop).
Python + SQLite + Supervisor, sem Docker, sem systemd e sem ferramentas do Termux.
Especificação completa: [AGENTS.md](AGENTS.md). Operação e recuperação: [docs/OPERACAO.md](docs/OPERACAO.md).

> Estado: código implementado e validado em **container x86_64 na nuvem**.
> No aparelho, **nada foi executado ainda** — veja [docs/VALIDACAO.md](docs/VALIDACAO.md).

## Instalação no celular (terminal do UserLAnd)

```bash
# 1) trazer o projeto para o disco PRIVADO do Linux (nunca rodar de /sdcard)
git clone -b ccr-ef8a999e-hdftdd https://github.com/estivendjgutti-dot/djgutti.git ~/djgutti
cp -rn ~/djgutti/estiven4tech-mobile-server ~/      # -n não sobrescreve arquivos que já existam
cd ~/estiven4tech-mobile-server

# 2) diagnóstico ANTES de instalar (gera docs/diagnostico-<data>.txt)
scripts/diagnostico.sh

# 3) dependências do sistema + venv + supervisor fixado + .env (chmod 600)
scripts/install.sh --apt

# 4) validar neste aparelho (sandbox; ~2 min; não toca dados reais)
scripts/validate.sh

# 5) iniciar
scripts/start.sh
```

## Comandos do dia a dia

| Ação | Comando |
|---|---|
| Iniciar (idempotente) | `scripts/start.sh` |
| Iniciar sem tmux | `scripts/start.sh --foreground` |
| Parar (com checkpoint) | `scripts/stop.sh` |
| Status / saúde | `scripts/status.sh` (`--rede` testa DNS dos hosts configurados) |
| Logs | `scripts/logs.sh` · `scripts/logs.sh -f` · `scripts/logs.sh todos -n 100` |
| Ver a tela do supervisor | `tmux attach -t e4t-server` (sair: `Ctrl+b` e depois `d`) |
| Reiniciar só o worker | `scripts/ctl.sh restart worker` |
| Backup agora | `scripts/backup.sh` |
| Testar restauração | `scripts/restore.sh backups/daily/<arquivo>.tar.gz --target /tmp/teste-restore` |
| Relatório de testes longos | `scripts/relatorio.sh --horas 8` |
| **Após reinício do Android/app** | abrir UserLAnd → sessão → `~/estiven4tech-mobile-server/scripts/start.sh` → `scripts/status.sh` |

`status.sh` retorna 0 (saudável), 1 (atenção) ou 2 (parado).

## O que roda

| Job | Tipo | Padrão |
|---|---|---|
| `demo_timestamp` | grava um timestamp no SQLite a cada 5 min | **ativo** (local) |
| `backup_diario` | backup online + retenção 7 diários / 4 semanais | **ativo** (local) |
| `http_check` | GET somente leitura numa URL pública | **desativado** (sem URL) |
| heartbeat externo | GET num endpoint de monitoramento | **desativado** (sem endpoint) |

Para personalizar: `cp config/jobs.example.json config/jobs.json`, editar e `scripts/ctl.sh restart worker`.
Configurações gerais em `.env` (modelo: `.env.example`). Variáveis exportadas no shell **não** chegam ao worker
através do tmux; use sempre o `.env`.

## Estrutura

```
src/worker.py     agendador + fila durável + execução (1 worker, concorrência 1)
src/db.py         SQLite: migrações, WAL/DELETE, estado
src/tasks.py      tarefas, classificação de erros, sanitização
src/backup.py     backup online, verificação, retenção, restauração
src/status.py     saúde lida do banco      src/report.py  relatório de agenda/lacunas/RSS
src/events_listener.py  registra FATAL/saídas inesperadas do supervisor
config/           supervisord.conf, jobs.example.json
scripts/          install, start, stop, status, logs, ctl, backup, restore, validate, relatorio, diagnostico
tests/            unitários (stdlib) + integração em sandbox
data/ logs/ run/ backups/   criados com chmod 700, fora do Git
```
