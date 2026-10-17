# Diagnóstico do ambiente

Regra: só fatos observados. O que não foi medido fica como **PENDENTE**.

## 1. Ambiente onde o código foi construído e validado (NÃO é o Galaxy)

A implementação de 17/10/2026 foi feita pelo Claude Code numa sessão **na nuvem**, não no celular.
Saída completa e sanitizada: [diagnostico-nuvem-x86_64-20261017.txt](diagnostico-nuvem-x86_64-20261017.txt).

| Item | Observado | Comando |
|---|---|---|
| Arquitetura | **x86_64** (o S10 Plus deve ser aarch64) | `uname -m` |
| Distribuição | Ubuntu 24.04.5 LTS (noble) | `cat /etc/os-release` |
| Usuário | root (uid 0) | `id` |
| Bits / libc | 64 / glibc 2.39 | `getconf LONG_BIT`, `getconf GNU_LIBC_VERSION` |
| Disco em $HOME | 31 GB livres (cota da sessão) | `df -h "$HOME"` |
| Memória | 15 GiB, sem swap | `free -h` |
| PID 1 | `process_api --firecracker-init` (micro-VM Firecracker) | `ps -p 1 -o pid,comm,args` |
| PRoot | **não**: `TracerPid: 0`, nenhuma variável `PROOT_*` | `/proc/self/status` |
| Python | 3.13.16 (venv); testes também em 3.11.x | `python3 --version` |
| tmux / SQLite | tmux 3.4 / SQLite 3.45.1 | `tmux -V`, `sqlite3 --version` |
| Supervisor | 4.3.0 (pip, venv), sem dependências extras | `supervisord --version` |
| flock, WAL, socket Unix, setitimer | todos OK no disco do projeto | `scripts/diagnostico.sh` |

Consequência: os testes daqui provam a lógica (fila, retry, recuperação, backup, scripts), mas **não** provam
o comportamento sob PRoot/aarch64/Android. Esses pontos precisam do diagnóstico abaixo, no aparelho.

## 2. Galaxy S10 Plus com UserLAnd — PENDENTE

Rodar no terminal do UserLAnd, **antes** de `install.sh --apt` e de novo depois:

```bash
cd ~/estiven4tech-mobile-server && scripts/diagnostico.sh
```

Copiar os resultados para a tabela (o arquivo completo fica em `docs/diagnostico-<data>.txt`).

| Item | Valor no aparelho | Observação |
|---|---|---|
| `uname -m` (esperado aarch64) | PENDENTE | se der armv7l/32 bits, parar e reavaliar |
| Distro e versão (`/etc/os-release`) | PENDENTE | preferir Debian 12 ou Ubuntu suportado |
| glibc | PENDENTE | |
| Usuário (`id`) | PENDENTE | root aparente ≠ root do Android |
| PRoot (`TracerPid` ≠ 0 / `PROOT_*`) | PENDENTE | |
| PID 1 | PENDENTE | |
| `df -h $HOME` (meta ≥ 8 GB livres) | PENDENTE | |
| `free -h` | PENDENTE | pode refletir o Android inteiro; não usar como RAM do app |
| Python / tmux / sqlite3 | PENDENTE | |
| flock no disco do projeto | PENDENTE | necessário para o lock do worker |
| SQLite WAL | PENDENTE | se falhar: `E4T_SQLITE_JOURNAL=delete` no `.env` (testado na nuvem) |
| Socket Unix | PENDENTE | se falhar, o supervisorctl não funciona; ver OPERACAO.md §6 |
| setitimer | PENDENTE | limite de duração das tarefas |
| tmux | PENDENTE | se falhar: `scripts/start.sh --foreground` |

### Informações que só as telas do Android mostram (preencher à mão)

| Item | Valor |
|---|---|
| Versão do Android / One UI | PENDENTE |
| RAM física | PENDENTE (S10 Plus: 8 GB ou 12 GB conforme modelo — confirmar em Configurações > Sobre o telefone) |
| Armazenamento livre | PENDENTE |
| Versão do UserLAnd | PENDENTE |
| Distribuição escolhida no app | PENDENTE |
| Origem da instalação do UserLAnd (Play Store/F-Droid) | PENDENTE |

## 3. UserLAnd Pro (backend Virtual Machine) — requisitos x S10 Plus

Contratado pelo proprietário em 17/10/2026. Requisitos publicados no blog oficial
(<https://blog.userland.tech/2026/09/actual-virtual-machines-come-to-android.html>):

| Requisito do modo VM | S10 Plus |
|---|---|
| Android 14 ou mais novo | Última atualização oficial do S10 Plus: Android 12 (One UI 4.1). **Confirmar** em Configurações > Sobre o telefone |
| ARM64 | Sim |
| Virtualização (AVF/pKVM) habilitada pelo fabricante | Improvável num aparelho com Android 12; o app checa a flag do aparelho |
| Configuração única via Depuração sem fio (ADB) + app "UserLAnd VM" | — |

Decisão de 17/10/2026: o proprietário informou que o Galaxy S20 FE, o outro aparelho disponível, está no
Android 13, que também fica abaixo do mínimo. Sem vantagem no modo VM, o servidor **continua no S10 Plus,
backend Standard (proot)**. Root, ROM customizada e kernel customizado foram avaliados e descartados:
quebrariam o Knox de forma permanente, apagariam os dados e muito provavelmente não liberariam o pKVM.

Conclusão: o backend VM **provavelmente não fica disponível no S10 Plus**. Root/ROM customizada
estão fora do escopo (AGENTS.md §1). O projeto continua no backend "Standard (proot)", que segue grátis.
Se um dia rodar em aparelho com VM, o mesmo código funciona; `scripts/diagnostico.sh` mostra o backend
detectado na seção "Backend do UserLAnd". Registrar aqui o que a tela do app mostrar.

## 4. Claude Code no UserLAnd — PENDENTE

Seguir AGENTS.md §11. Registrar aqui `claude --version`, `claude doctor` e qualquer erro (sem segredos).
Se não funcionar sob PRoot, o servidor Python funciona sem ele: manutenção pode ser feita de outra máquina.
