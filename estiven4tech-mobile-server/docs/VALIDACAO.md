# Validação — resultados reais e pendências

Legenda: **OK-NUVEM** = executado e aprovado no container x86_64 de desenvolvimento (não é o celular);
**PENDENTE** = ainda não executado; nenhum item do aparelho foi executado até agora.
Nada aqui deve ser lido como "validado no Galaxy" até a coluna do aparelho estar preenchida.

## 1. Testes automáticos (curtos)

Executados em 17/10/2026 ~19:00 UTC com `scripts/validate.sh` (23 testes unitários + 14 checagens de integração
em sandbox com supervisor e tmux reais). Unitários também aprovados com Python 3.11 (versão do Debian 12).
Integração repetida com `E4T_SQLITE_JOURNAL=delete` (fallback do PRoot): 14/14, log confirmou `journal=delete`.

| # AGENTS §10 | Teste | Nuvem (17/10) | Evidência na nuvem | Galaxy |
|---|---|---|---|---|
| 1 | `start.sh` duas vezes → 1 supervisord, 1 worker, 1 agenda | OK-NUVEM | supervisord=[2823] worker=[2832]; 2º start: "Nada a fazer" | PENDENTE |
| 2 | Demo persistida no SQLite | OK-NUVEM | eventos/runs/slots = (2, 2, 2) | PENDENTE |
| 3 | `kill -9` só no worker → restart sem duplicar | OK-NUVEM | pid 2879→2899; eventos=3, slots distintos=3; `events.log` registrou a saída | PENDENTE |
| 4 | Stub local HTTP: 429 (Retry-After), 500 (backoff, 5 tentativas → dead), timeout, 404, rede fora, sanitização | OK-NUVEM | `tests/test_worker.py` ExecutionTests (9 testes) | PENDENTE |
| 5 | Queda durante tarefa com efeito externo | OK-NUVEM | run `envio_simulado` → `needs_review`, attempts=1, não reexecutado; status.sh rc=1 | PENDENTE |
| 5 | Queda durante tarefa local | OK-NUVEM | volta à fila, executa uma vez (RecoveryTests) | PENDENTE |
| 6 | Backup online com worker ativo + restauração em pasta temporária | OK-NUVEM | integrity=ok; contagens iguais ao manifesto {demo_events:4, jobs:2, runs:6, state:5}; sem .env/.venv | PENDENTE |
| 6 | Restauração de produção recusada com servidor ativo; aceita parado | OK-NUVEM | recusa com "stop.sh"; banco anterior salvo em `data/pre-restore-*` | PENDENTE |
| — | Heartbeat antigo com processo vivo (SIGSTOP) | OK-NUVEM | status.sh rc=1 "possivelmente travado" | PENDENTE |
| — | Falha contínua → FATAL limitado e registrado | OK-NUVEM | worker FATAL após 5 tentativas; `events.log` com FATAL | PENDENTE |
| — | `stop.sh` duas vezes, checkpoint | OK-NUVEM | "já estava parado"; `clean_shutdown=1`; nenhum processo restante | PENDENTE |
| — | Agenda: UNIQUE, catch-up latest/skip/window, relógio voltando, atraso máximo | OK-NUVEM | SchedulingTests (7 testes) | PENDENTE |
| — | Lock exclusivo do worker; SIGTERM com checkpoint | OK-NUVEM | ProcessTests | PENDENTE |
| — | Retenção 7 diários + 4 semanais | OK-NUVEM | BackupTests | PENDENTE |
| — | Retomada opcional no `.bashrc` (ativar/desativar, idempotente, backup) | OK-NUVEM | testado num HOME temporário; não ativado | n/a |
| 10 | RSS supervisord + worker + listener | OK-NUVEM: 57,9 MB (sandbox), 60,9 MB (instância normal) | `scripts/relatorio.sh` | PENDENTE |

Rodar no aparelho e colar o resumo aqui:

```bash
cd ~/estiven4tech-mobile-server && scripts/validate.sh 2>&1 | tee docs/validacao-galaxy-$(date +%Y%m%d).txt
```

Se algo falhar no PRoot, registrar o erro sanitizado e seguir o plano B de docs/OPERACAO.md §6.

## 2. Verificações manuais do Android (só o proprietário pode fazer)

Itens de AGENTS.md §4. Marcar o nome real da opção no seu One UI.

| Item | Feito? | Nome da opção no aparelho / observação |
|---|---|---|
| UserLAnd oficial, sessão Debian/Ubuntu sem desktop | PENDENTE | |
| Notificações do UserLAnd permitidas | PENDENTE | |
| Bateria do app: sem restrições / segundo plano | PENDENTE | |
| Limites de uso em segundo plano: "nunca suspender" | PENDENTE | |
| Economia de energia desligada; dados em segundo plano | PENDENTE | |
| "Manter aberto" em Recentes (se existir) | PENDENTE | |
| Proteção de bateria / ventilação | PENDENTE | |

## 3. Testes longos com o aparelho — NÃO EXECUTADOS

Nenhum destes foi executado. Não marcar como aprovado antes de cumprir o tempo inteiro.

| # | Teste | Como executar | Resultado |
|---|---|---|---|
| 7a | Tela apagada 30–60 min | `scripts/start.sh`, anotar hora, apagar a tela; ao voltar: `scripts/status.sh` e `scripts/relatorio.sh --horas 1` | PENDENTE |
| 7b | Tela apagada 8 h | idem; `scripts/relatorio.sh --horas 8` | PENDENTE |
| 7c | 24 h | idem; `scripts/relatorio.sh --horas 24` | PENDENTE |
| 8 | Wi-Fi desligado 5 min | ativar `http_check` com URL pública sua; desligar Wi-Fi 5 min; religar; `scripts/status.sh` e `scripts/logs.sh -n 50`: esperado retry com espera e só a verificação mais recente ao voltar | PENDENTE |
| 9a | Fechar/reabrir a sessão do UserLAnd | fechar o terminal; reabrir; `scripts/status.sh` (o tmux sobreviveu?) | PENDENTE |
| 9b | Android encerra o app / reiniciar o telefone | reiniciar; cronometrar do desbloqueio até `scripts/status.sh` saudável | PENDENTE |
| 10 | RSS/disco/carga com Claude Code aberto | `scripts/relatorio.sh` durante uma sessão do Claude Code | PENDENTE |

O que olhar no relatório: slots esperados × `succeeded` × `skipped`, atraso p95 (meta < 60 s),
lacunas (onde o Android pausou o app), efeitos duplicados (meta 0), RSS (meta < 500 MB).

Modelo de registro:

| Data | Teste | Resultado | Evidência (saída do relatório/status) | Pendência |
|---|---|---|---|---|
| | | | | |

## 4. Limitações conhecidas do que foi validado

- Ambiente de teste: micro-VM x86_64 com kernel próprio, sem PRoot, sem Android. Comportamento de
  ptrace/PRoot, aarch64, Doze e economia de bateria não foi observado.
- O watchdog interno (saída com código 70 se o laço travar) foi revisado no código, mas não há teste que
  force esse travamento; o caso de processo inteiro congelado foi testado via SIGSTOP + status.sh.
- Claude Code no UserLAnd: não testado (AGENTS.md §11).
