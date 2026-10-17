# Operação, recuperação, Android e migração

Todos os comandos assumem `cd ~/estiven4tech-mobile-server`. Os scripts resolvem o próprio caminho,
então também funcionam chamados de qualquer pasta (`~/estiven4tech-mobile-server/scripts/status.sh`).

## 1. Comandos

| Ação | Comando |
|---|---|
| Instalar/atualizar dependências | `scripts/install.sh --apt` (idempotente) |
| Iniciar | `scripts/start.sh` |
| Iniciar sem tmux | `scripts/start.sh --foreground` (o terminal fica ocupado; `Ctrl+C` encerra com checkpoint) |
| Parar | `scripts/stop.sh` |
| Status | `scripts/status.sh` · `scripts/status.sh --rede` · `scripts/status.sh --json` |
| Logs | `scripts/logs.sh [worker\|supervisor\|events\|todos] [-f] [-n N]` |
| supervisorctl | `scripts/ctl.sh status` · `scripts/ctl.sh restart worker` |
| Tela do supervisor | `tmux attach -t e4t-server` (sair sem parar: `Ctrl+b`, `d`) |
| Backup | `scripts/backup.sh` |
| Restaurar (teste) | `scripts/restore.sh <backup.tar.gz> --target /tmp/teste-restore` |
| Restaurar (produção) | `scripts/stop.sh && scripts/restore.sh <backup.tar.gz> --production` |
| Validação automática | `scripts/validate.sh` |
| Relatório de testes longos | `scripts/relatorio.sh --horas 24` |

## 2. Como funciona

- **Um** supervisord (`supervisord -n`) dentro da sessão tmux `e4t-server`, controlado por socket Unix
  privado em `run/supervisor.sock` (chmod 700). Não há painel HTTP nem porta aberta.
- **Um** worker Python. O agendador é interno: não use cron para os mesmos jobs.
- `start.sh` só inicia algo se o supervisord não responder no socket; confere PID pelo `cmdline`
  (não confia em PID solto); encerra worker órfão que ainda segure `run/worker.lock`.
- O worker pega `flock` exclusivo em `run/worker.lock`; uma segunda cópia sai com código 3.
- Agenda vem do banco (`jobs.last_slot_utc`), alinhada a UTC: o job de 5 min roda em :00, :05, :10…
  O backup diário roda à 00:00 UTC (21:00 em Brasília) e logo na primeira partida.
- `UNIQUE(job_id, scheduled_at_utc)` impede enfileirar o mesmo horário duas vezes.
- Falhas transitórias (rede, timeout, HTTP 408/425/429/5xx): retry exponencial com jitter (30 s, 60 s, 120 s…,
  teto 1 h), respeitando `Retry-After`, máximo 5 tentativas → status `dead`. HTTP 4xx → `dead` imediato.
- Execução interrompida (queda, `kill -9`, Android): no próximo início, tarefas locais voltam à fila;
  tarefas marcadas `external_effect: true` viram `needs_review` e **não** são reexecutadas.
- Política de atraso por job (`catchup`): `latest` (só a mais recente ao voltar — monitoramento),
  `window` (atrasadas dentro de `catchup_window_seconds`, máx. 50 — relatórios),
  `skip` (nenhuma atrasada — mensagens). `max_lateness_seconds` descarta execuções velhas demais.
  Slots perdidos ficam registrados como `skipped` (visíveis no status e no relatório).
- Heartbeat local no SQLite a cada 60 s. Watchdog interno: se o laço principal parar de progredir,
  o worker sai com código 70 e o supervisor reinicia. Se o processo inteiro congelar, `status.sh`
  acusa "processo vivo mas possivelmente travado"; corrija com `scripts/ctl.sh restart worker`.
- Supervisor: `autorestart=unexpected`, `startsecs=5`, `startretries=5`. Falha contínua termina em **FATAL**
  (sem loop infinito) e fica registrada em `logs/events.log`.
- Logs: `logs/worker.log`, `logs/supervisord.log`, `logs/events.log`, 5 MB × 3 cada, com sanitização
  de tokens, senhas, query strings e credenciais em URLs.

## 3. Recuperação

| Evento | O que fazer |
|---|---|
| Worker caiu | Nada: o supervisor reinicia. Conferir `scripts/status.sh`. |
| Worker em FATAL | `scripts/logs.sh worker -n 100` → corrigir a causa → `scripts/start.sh` (reinicia só o worker). |
| `status.sh` diz "possivelmente travado" | `scripts/ctl.sh restart worker`; anotar data/hora em VALIDACAO.md. |
| Wi-Fi caiu | Nada: HTTP entra em retry; ao voltar, só a verificação mais recente roda. |
| Terminal fechado/desconectado | Reabrir a sessão no UserLAnd e `scripts/status.sh`. Se o app continuou vivo, o tmux manteve tudo. |
| **Android matou o UserLAnd** | Abrir UserLAnd → sessão → `~/estiven4tech-mobile-server/scripts/start.sh` → `scripts/status.sh`. |
| **Telefone reiniciou** | Desbloquear → abrir UserLAnd → sessão → `scripts/start.sh` → `scripts/status.sh`. |
| `needs_review` no status | Conferir no destino externo se a ação aconteceu; depois marcar (ver abaixo). |
| Banco corrompido | `scripts/stop.sh` → `scripts/restore.sh backups/daily/<mais recente> --production` → `scripts/start.sh`. |

Marcar uma execução conciliada (substitua o ID):

```bash
.venv/bin/python -c "import sqlite3; c=sqlite3.connect('data/app.db'); c.execute(\"UPDATE runs SET status='succeeded', error='conciliado manualmente' WHERE id=123 AND status='needs_review'\"); c.commit()"
```

Faça isso com o servidor parado (`scripts/stop.sh`) para manter um único escritor.

### Retomada ao abrir a sessão (opcional, desativada)

`scripts/retomada-bashrc.sh ativar` acrescenta ao `~/.bashrc` um bloco marcado que chama `start.sh`
(idempotente) ao abrir um terminal interativo fora do tmux. Faz backup do `.bashrc` antes.
Desfazer: `scripts/retomada-bashrc.sh desativar`. Isso **não** abre o UserLAnd após o boot.

## 4. Android (manual — o Linux não consegue ajustar isso)

Registrar em VALIDACAO.md os nomes que realmente existem no seu One UI.

1. UserLAnd instalado pela distribuição oficial; sessão Debian/Ubuntu em modo terminal, sem VNC/desktop.
2. Notificações do UserLAnd permitidas; manter a notificação de sessão ativa.
3. Configurações > Aplicativos > UserLAnd > Bateria: **Sem restrições** / permitir atividade em segundo plano.
4. Configurações > Bateria > Limites de uso em segundo plano: tirar UserLAnd de "suspensos"/"suspensão profunda"
   e incluir em "nunca suspender". Incluir o cliente de terminal/SSH, se usar outro.
5. Desligar economia de energia durante o uso como servidor; permitir dados em segundo plano; Wi-Fi estável.
6. Em Recentes, "Manter aberto"/bloquear o app, se existir (não substitui o teste de tela apagada).
7. Nunca usar "Fechar tudo" nem "Forçar parada" no UserLAnd.
8. Ventilação; proteger bateria (limite de 85%, se houver); pausar se aquecer demais ou a bateria estufar.
9. Não é preciso tela acesa; o comportamento real com tela apagada será medido nos testes longos.

Limites conhecidos: tmux e supervisor só protegem enquanto o Android mantém o UserLAnd vivo.
`nohup` não resolve a morte do app. Após reboot, é preciso desbloquear e abrir o app manualmente;
autoabertura só deve ser prometida se a sua versão do app oferecer e um teste no aparelho comprovar.
Um heartbeat externo pode **avisar** que o celular sumiu, mas não o ressuscita.

## 5. Rede e integrações

- Só conexões de saída. Nenhuma porta aberta; nada escuta na internet.
- `http_check` e heartbeat externo vêm desativados. Para ativar:
  - `http_check`: copiar `config/jobs.example.json` para `config/jobs.json`, preencher `url` pública,
    `enabled: true`, `scripts/ctl.sh restart worker`.
  - Heartbeat externo: no `.env`, `E4T_HEARTBEAT_URL=https://...` e `E4T_HEARTBEAT_ENABLED=1`; reiniciar o worker.
- Qualquer automação que envie mensagens ou altere sistemas externos deve ter `external_effect: true`,
  chave de idempotência quando a API suportar e `catchup: skip`. Só ativar com credenciais e autorização.
- Variáveis exportadas no shell não atravessam um servidor tmux já existente: configure tudo no `.env`.

## 6. Plano B se tmux ou sockets falharem no PRoot

1. tmux falha → `scripts/start.sh --foreground` numa sessão dedicada do UserLAnd. Abra outra sessão
   para `status.sh`/`logs.sh`.
2. Socket Unix falha → o supervisorctl não conecta. Rodar o worker direto, sem supervisor
   (sem reinício automático): `.venv/bin/python src/worker.py` em foreground. Registrar a limitação.
3. WAL falha → `E4T_SQLITE_JOURNAL=delete` no `.env` (testado na nuvem; mantém um único escritor).

## 7. Backup e exportação

- Diário automático (job `backup_diario`) e manual (`scripts/backup.sh`), via API online do SQLite —
  seguro com o worker rodando e com WAL. Inclui código/config/docs e o banco; **não** inclui `.venv`,
  caches nem `.env`.
- Retenção: 7 diários (o mais recente de cada dia) + 4 semanais (`E4T_BACKUP_DAILY_KEEP`/`WEEKLY_KEEP`).
- Backup local não protege da perda do celular. Exportação manual sugerida (semanal):
  copiar o arquivo mais recente de `backups/weekly/` para um destino seu (PC, nuvem pessoal).
  Exemplo para a memória compartilhada (confirme o caminho que o UserLAnd expõe, ex. `/storage/internal`):
  `cp backups/weekly/$(ls backups/weekly | tail -1) /storage/internal/Download/`
  Sincronização automática só depois de você definir destino e credenciais.

## 8. Migração para outro host

1. `scripts/stop.sh` no celular.
2. `scripts/backup.sh` e copiar o `.tar.gz` para o destino.
3. No destino: extrair o código (ou clonar), `scripts/install.sh --apt`,
   `scripts/restore.sh <backup> --production`, recriar `.env` à mão (não vem no backup).
4. `scripts/validate.sh`, depois `scripts/start.sh`.
5. Manter **uma** instância ativa. Nunca rodar celular e VPS com os mesmos efeitos externos ao mesmo tempo.

## 9. Atualizar o código

```bash
cd ~/djgutti && git pull
~/estiven4tech-mobile-server/scripts/stop.sh
~/estiven4tech-mobile-server/scripts/backup.sh
(cd ~/djgutti/estiven4tech-mobile-server && tar cf - --exclude=./.env --exclude=./data --exclude=./logs \
  --exclude=./run --exclude=./backups --exclude=./.venv --exclude=./config/jobs.json .) \
  | (cd ~/estiven4tech-mobile-server && tar xf -)
~/estiven4tech-mobile-server/scripts/install.sh && ~/estiven4tech-mobile-server/scripts/start.sh
```

Isso substitui os arquivos de código pelos do repositório; `.env`, `config/jobs.json`, dados, logs e
backups são preservados. Outras edições locais ficam guardadas no backup feito antes.

Se o repositório for privado, o `git clone/pull` no celular pede autenticação (token pessoal do GitHub,
digitado no prompt, nunca em linha de comando). Alternativa: baixar o ZIP do GitHub no Android e copiar
da memória compartilhada para `~` dentro do UserLAnd.
