# Secretária virtual de TESTE no celular

Uma cópia do app `secretaria-barber/poc-whatsapp`, rodando no UserLAnd **sem Docker**, para medir se o
celular aguenta a secretária antes de migrar qualquer automação. O código do app não é alterado.

## O que é igual e o que é diferente da VPS

| | VPS (barber.estiven4tech.com.br) | Celular (teste) |
|---|---|---|
| Código | `poc-whatsapp` | o mesmo, clonado em `~/secretaria-barber` |
| Execução | Docker Compose | supervisord do projeto (`sec-postgres`, `sec-app`, `sec-worker`) |
| Node | imagem `node:22.12.0-alpine` | Node 22.22.0 oficial, baixado com SHA-256 conferido, em `~/.local/opt` |
| Banco | Postgres 16 em contêiner | PostgreSQL do apt (15 no Debian 12, 16 no Ubuntu 24.04), **só socket Unix**, sem porta TCP |
| Porta do app | 127.0.0.1:3100 (nginx na frente) | 127.0.0.1:3100, forçado por `secretaria/bind-loopback.mjs` |
| WhatsApp | Meta / WAHA reais | **desligado** (`WHATSAPP_SEND_MODE=off`, WAHA vazio) |
| IA | OpenAI | resposta fixa de teste (sem `OPENAI_API_KEY`) |
| E-mail, Google Agenda | configurados | desligados |
| Segredos | os de produção | **aleatórios e locais**, gerados em `.env.secretaria` (chmod 600) |
| Webhook público | sim | não: as mensagens vêm do simulador assinado do próprio app |

Desvio consciente do AGENTS.md §3 ("não adicionar PostgreSQL/Node sem necessidade demonstrada"): a
necessidade é reproduzir o app real, que depende dos dois. Ambos ficam isolados neste módulo opcional.

Travas em `secretaria/run-node.sh`: o app e o worker **não sobem** se `WHATSAPP_SEND_MODE` for diferente de
`off`, ou se `WAHA_URL` ou `RESEND_API_KEY` estiverem preenchidos, a menos que `.env.secretaria` tenha
`SECRETARIA_PERMITIR_ENVIO_REAL=sim`. Só mude isso com número de teste e autorização explícita.

## Instalação no celular

Pré-requisito: servidor base instalado (README) e terminal como **usuário normal** (o Postgres recusa root).

```bash
sudo apt install -y postgresql curl xz-utils
git clone https://github.com/estivendjgutti-dot/secretaria-barber.git ~/secretaria-barber   # repositório privado: pede login
cd ~/estiven4tech-mobile-server
scripts/diagnostico.sh          # olhar "SysV shm" — se FALHOU, o Postgres não sobe neste PRoot (ver Riscos)
scripts/secretaria.sh instalar  # Node 22, npm ci, cluster Postgres, migrações, .env.secretaria
scripts/secretaria.sh testar    # typecheck + 183 testes do app (esperado: 4 falhas de data, ver abaixo)
scripts/start.sh
scripts/secretaria.sh ativar    # sobe sec-postgres, sec-app e sec-worker no supervisor
scripts/secretaria.sh status
scripts/secretaria.sh simular   # conversa ponta a ponta simulada
```

Painel: no navegador do próprio celular, `http://127.0.0.1:3100`. Criar um operador de teste
(sem chave do Resend, nenhum e-mail sai; o link de convite de 72 h é impresso no terminal):
`scripts/secretaria.sh operador seu-email@exemplo.com "Gutti"`.

## Teste contínuo

```bash
cp config/jobs.secretaria-teste.json config/jobs.json && scripts/ctl.sh restart worker
```

O agendador passa a rodar, além da demo e do backup diário:

| Job | Frequência | O que prova |
|---|---|---|
| `sec_healthz` | 1 min | app respondendo em 127.0.0.1:3100 |
| `sec_simulacao` | 15 min | webhook assinado → fila → worker → estado da conversa → banco, de ponta a ponta |
| `sec_backup` | diário | `pg_dump` legível, retenção de 7 |

Acompanhar: `scripts/status.sh`, `scripts/secretaria.sh status`,
`scripts/relatorio.sh --job sec_simulacao --horas 8`, `scripts/logs.sh -n 50`,
`tail -n 50 logs/sec-app.log`. Voltar ao padrão: `rm config/jobs.json && scripts/ctl.sh restart worker`.
Desligar a secretária: `scripts/secretaria.sh desativar` (dados preservados).

## Comandos

| Ação | Comando |
|---|---|
| Instalar / reinstalar dependências | `scripts/secretaria.sh instalar` (idempotente) |
| Ligar / desligar no supervisor | `scripts/secretaria.sh ativar` / `desativar` |
| Saúde (processos, banco, /healthz, porta só loopback, travas) | `scripts/secretaria.sh status` |
| Testes do app | `scripts/secretaria.sh testar` → `logs/sec-testes-ultima.txt` |
| Simulação | `scripts/secretaria.sh simular` → `logs/sec-simulacao-ultima.txt` |
| Migrações após atualizar o código | `scripts/secretaria.sh migrar` |
| Backup do banco | `scripts/secretaria.sh backup` → `backups/secretaria/` |
| Operador do painel (link impresso) | `scripts/secretaria.sh operador EMAIL "Nome"` |
| Console SQL | `scripts/secretaria.sh psql` |
| Logs | `logs/sec-app.log`, `logs/sec-worker.log`, `logs/sec-postgres.log` |

Após reinício do Android: abrir UserLAnd → `scripts/start.sh` sobe tudo, inclusive a secretária ativada.

## Riscos específicos do celular

1. **Memória compartilhada System V.** O PostgreSQL precisa dela, e o kernel do Android geralmente não tem;
   funciona só se o PRoot do UserLAnd emular. `scripts/diagnostico.sh` testa (`SysV shm: OK/FALHOU`).
   Se falhar, o Postgres não sobe nesse aparelho e a secretária completa não roda nele sem reescrever
   a camada de banco. Isso decide a viabilidade, então é o primeiro teste a fazer.
2. **Memória.** Na nuvem o conjunto (servidor base + Postgres + app + worker) somou ~400 MB de RSS,
   perto da meta de 500 MB. No S10 Plus, observar `scripts/relatorio.sh` e o comportamento do Android.
3. **Phantom process killer (Android 12).** São ~8 processos filhos; o Android pode encerrá-los.
4. **Dependências nativas.** `npm ci` baixa o esbuild para arm64; confirmar no aparelho.

## Problema encontrado no repositório secretaria-barber (não corrigido aqui)

4 testes de `test/agenda.int.test.ts` falham **em qualquer ambiente desde 17/10/2026 13:00 UTC**: eles agendam
um horário fixo (sábado 17/10 10:00 de Brasília) e depois cancelam, mas `agenda.cancel` exige
`starts_at > now()` com o relógio real do banco. Hoje esse horário já passou. Correção sugerida no repositório
do app: datas relativas ao relógio do banco nos testes, ou injetar o "agora" também no cancelamento.
`scripts/secretaria.sh testar` mostra essas 4 falhas; as outras 179 devem passar.
