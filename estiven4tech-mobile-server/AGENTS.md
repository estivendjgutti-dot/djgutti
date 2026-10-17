# Galaxy S10 Plus — servidor de automações com UserLAnd

Responsável: Estiven / Estiven4tech. Planejamento de 17/10/2026.
Este arquivo é a especificação e as instruções para o agente que fará a implantação no aparelho.
Estado inicial: PLANEJADO. Nada foi instalado ou validado no Galaxy por esta conversa.

## 1. Objetivo e autorização

Transformar o Galaxy S10 Plus em um host Linux leve, pelo UserLAnd, para executar automações pessoais continuamente e recuperar seu estado após interrupções.
O proprietário conhece as limitações do Android e quer prosseguir. Execute a implementação local necessária, em etapas, sem pedir confirmação repetida sobre esse risco.
Não alterar VPS, DNS, serviços existentes nem enviar mensagens reais a terceiros sem autorização específica. Não fazer root ou desbloquear o bootloader.
O agente deve comunicar em pt-BR, registrar os comandos efetivamente executados e diferenciar planejamento, execução e validação.

## 2. Premissas técnicas

- Usar sessão padrão Linux do UserLAnd, sem desktop/VNC. Preferir Debian ou Ubuntu com pacotes ainda suportados; verificar a versão realmente entregue pelo app.
- Tratar o ambiente como Linux em espaço de usuário sobre Android, normalmente via PRoot, e não como VPS com kernel próprio.
- Confirmar modo de execução real antes de decidir ferramentas. Não presumir VM, Docker, systemd, namespaces, cgroups, iptables ou acesso a hardware.
- O root aparente dentro da distribuição não significa root do Android.
- Confirmar ARM64/aarch64 no ambiente instalado; não inferir só pelo modelo do telefone.
- Não substituir UserLAnd por Termux. Comandos pkg, termux-wake-lock e Termux:Boot não pertencem a este plano.
- Reinício de processo e reinício do app são problemas distintos: o supervisor só atua enquanto o ambiente Linux existe.
- Claude Code é ferramenta de implantação/manutenção; não precisa ficar aberto para as automações funcionarem.
- Claude Code documenta Linux ARM64, mas não garante UserLAnd/PRoot. Validar o binário e o login antes de depender dele.

## 3. Arquitetura escolhida

| Camada | Escolha inicial | Papel |
|---|---|---|
| Android | UserLAnd e terminal de sessão | Manter o Linux disponível |
| Linux | Debian/Ubuntu ARM64, sem desktop | Ambiente de execução |
| Supervisão | Python Supervisor em modo foreground, dentro de tmux | Reiniciar processos que falham |
| Automações | Python em venv, um worker | Requisições HTTP e tarefas agendadas |
| Persistência | SQLite em disco privado do Linux | Fila, histórico e checkpoints |
| Operação | Scripts start/stop/status/backup | Retomada simples |
| Verificação externa | Heartbeat HTTPS opcional | Detectar telefone fora do ar |

Começar com um processo worker e um único agendador integrado. Não usar cron e outro agendador para a mesma tarefa.
SQLite é a escolha inicial por reduzir consumo; se houver incompatibilidade de locking/WAL em PRoot, usar journal tradicional e testar, mantendo um único escritor.
Não adicionar Redis, PostgreSQL, interface web ou Node.js sem necessidade demonstrada.
Não instalar n8n no MVP. Se solicitado depois, validar os requisitos atuais de Node e n8n no ambiente, instalar diretamente sem Docker, medir recursos e substituir o agendador existente em vez de duplicá-lo.

## 4. Configuração manual do Android

Orientar Estiven a executar os itens abaixo. Os nomes variam conforme Android/One UI; registrar os que realmente existem.

1. Instalar/abrir UserLAnd por sua distribuição oficial e criar sessão Debian/Ubuntu com terminal/SSH, sem interface gráfica.
2. Permitir notificações relevantes do app e manter a sessão ativa; verificar a notificação de serviço quando disponível.
3. Em Informações do app > Bateria, permitir atividade em segundo plano/uso sem restrições quando disponível.
4. Em Bateria > Limites de uso em segundo plano, retirar UserLAnd dos apps suspensos e adicionar aos que nunca suspendem, se a opção existir. Incluir o cliente de terminal quando necessário.
5. Desativar economia de energia durante o uso como servidor e permitir dados em segundo plano. Usar Wi-Fi estável.
6. Se houver opção de manter aberto nos aplicativos recentes, ativá-la. Isso não substitui o teste com tela apagada.
7. Não usar Fechar tudo nem Forçar parada no app durante a operação.
8. Manter ventilado, evitar carga computacional intensa durante carregamento e habilitar proteção de bateria, se disponível. Pausar se houver aquecimento anormal ou bateria estufada.
9. Não exigir tela permanentemente acesa; validar o comportamento real com tela apagada.

O agente no Linux não consegue conceder essas permissões do Android. Registrar esses itens como manuais até o proprietário confirmar.

## 5. Fase A — diagnóstico obrigatório

Executar antes de instalar dependências; não imprimir variáveis de ambiente, tokens ou arquivos de credenciais.

```bash
uname -m
cat /etc/os-release
id
getconf LONG_BIT
getconf GNU_LIBC_VERSION
df -h "$HOME"
free -h
ps -p 1 -o pid,comm,args
date -Is
command -v python3
command -v tmux
```

Algumas leituras de /proc podem falhar ou refletir o Android inteiro. Registrar a limitação, sem inventar RAM disponível para o app.
Confirmar versão do Android/One UI, RAM física, armazenamento livre e versão do UserLAnd pelas telas do aparelho.
Meta inicial de planejamento: reservar pelo menos 8 GB livres e começar com apenas uma automação. Não é requisito oficial do UserLAnd.
Se a distribuição estiver sem suporte, preferir imagem suportada do app. Não fazer upgrade de distribuição às cegas nem misturar repositórios.
Produzir docs/DIAGNOSTICO.md com fatos e pendências. Se uma dependência falhar, registrar erro sanitizado e adaptar o plano.

## 6. Fase B — base do projeto

Diretório padrão: ~/estiven4tech-mobile-server, no armazenamento privado do Linux. Não executar projeto, venv ou SQLite em /sdcard ou pasta compartilhada.
Não sobrescrever arquivos existentes: inspecionar e preservar alterações do usuário.
Instalar via apt, usando sudo apenas quando necessário e disponível:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git ripgrep tmux python3 python3-venv python3-pip sqlite3 tzdata
mkdir -p ~/estiven4tech-mobile-server
cd ~/estiven4tech-mobile-server
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install supervisor
```

Se já estiver como root dentro do Linux, usar apt sem sudo. O usuário cotidiano deve ser não root sempre que viável.
Não rodar upgrade geral, instalar pacotes pesados ou modificar o sistema além do necessário.
Fixar versões das dependências efetivamente testadas em requirements.lock; verificar compatibilidade com o Python instalado.
Usar TZ=America/Sao_Paulo para operação, horários persistidos em UTC e exibição local. Não depender de systemd-timedated.

Criar os seguintes arquivos durante a implantação:

| Caminho | Entrega |
|---|---|
| CLAUDE.md | Importação @AGENTS.md, preservando conteúdo preexistente |
| README.md | Instalação e comandos curtos para celular |
| requirements.lock | Versões testadas |
| .env.example | Configurações sem credenciais |
| .gitignore | Excluir .env, .venv, data, logs, backups e segredos |
| src/worker.py | Agendador/fila durável e execução |
| src/db.py | Migrações e acesso SQLite |
| config/jobs.example.json | Exemplos inicialmente desativados |
| config/supervisord.conf | Supervisor sem serviço systemd |
| scripts/start.sh | Inicialização idempotente |
| scripts/stop.sh | Encerramento com checkpoint |
| scripts/status.sh | Processos, último sucesso, fila e heartbeat |
| scripts/backup.sh | Backup consistente e retenção |
| scripts/restore.sh | Restauração explícita, com processo parado |
| docs/DIAGNOSTICO.md | Hardware/ambiente observado |
| docs/OPERACAO.md | Recuperação, Android e migração |
| docs/VALIDACAO.md | Resultados reais e pendências |

Criar data/, logs/, run/ e backups/ com permissões restritas. Scripts devem resolver o diretório do projeto sem depender do diretório de chamada.
Proteger .env com chmod 600. Não colocar tokens em comandos, Git, mensagens ou logs.

## 7. Fase C — comportamento das automações

MVP sem credenciais: automação local de demonstração que registra um timestamp a cada 5 minutos e uma verificação HTTP somente leitura opcional de URL pública configurada.
Monitoramento de sites, sincronização via APIs oficiais e relatórios são expansões; não ligar integrações reais automaticamente.

Requisitos do worker:

- Fila durável SQLite: jobs, runs e estado; cada execução tem job_id, scheduled_at_utc, status, attempts, next_retry_at, started_at, finished_at e erro sanitizado.
- Restrição única por job_id + scheduled_at_utc para impedir enfileiramento duplicado.
- Concorrência inicial 1; tarefas com limite de duração e chamadas HTTP com timeout explícito.
- Retry exponencial com jitter, máximo 5 tentativas; respeitar Retry-After quando aplicável; falhas permanentes devem ficar visíveis.
- Recuperar registros running interrompidos com lease/expiração; não reexecutar automaticamente ações externas ambíguas.
- Para efeitos externos usar chave de idempotência quando suportada e conciliação de resultado quando não suportada. Não prometer exactly-once só com SQLite.
- Política de atraso configurável: monitoramento executa uma verificação recente ao voltar; relatórios usam janela de recuperação limitada; mensagens antigas não são enviadas em massa ao reconectar.
- Encerramento por SIGTERM com checkpoint; reinício reconstrói agenda a partir do banco, não da memória.
- Usar relógio monotônico para esperas e UTC para agenda; tratar mudanças de relógio e rede indisponível.
- Registrar heartbeat local a cada 60 segundos; heartbeat externo é configurável e permanece desativado até receber endpoint.
- Logs com rotação, por exemplo 5 MB x 3 arquivos por processo, sem dados pessoais ou tokens.

Não depender de Claude Code, chamadas a modelos ou assinatura Claude para manter o agendador rodando. Automações que usam IA por API terão credenciais e cobrança próprias.

## 8. Fase D — supervisor e retomada

Usar supervisord do venv com configuração explícita, caminho absoluto, socket Unix privado e sem painel HTTP aberto.
Configurar autostart, autorestart=unexpected, startsecs, startretries limitados, stopsignal=TERM, stopasgroup=true e killasgroup=true. Registrar quando entrar em FATAL; não criar loop de falhas sem limite.
Executar supervisord -n em uma sessão tmux dedicada. tmux facilita sair da tela do terminal, mas não impede encerramento pelo Android.
start.sh deve detectar a instância existente por socket/PID validado e não iniciar duas cópias. Usar também lock no worker, validando suporte a locking no sistema de arquivos local.
status.sh deve diferenciar processo vivo, tarefa parada/travada e ausência de rede. PID vivo sozinho não é prova de saúde.
Se tmux ou sockets falharem em PRoot, testar supervisor foreground diretamente na sessão e documentar a limitação. Não afirmar que nohup resolve a morte do app.
Não adicionar início automático a .bashrc incondicionalmente. Se oferecer retomada ao abrir a sessão, fazê-la chamar start.sh idempotente e preservar o arquivo original.

| Evento | Recuperação prevista |
|---|---|
| Worker falha | Supervisor reinicia dentro dos limites |
| Wi-Fi cai | Worker persiste e tenta novamente com espera |
| Terminal desconecta | tmux pode manter sessão se UserLAnd continuar vivo; testar |
| Android mata UserLAnd | Reabrir app e sessão; executar start.sh; recuperar banco |
| Telefone reinicia | Desbloquear, abrir UserLAnd/sessão e executar start.sh |
| Processo parece vivo mas travou | Detectar heartbeat antigo; reinício controlado e registro |

Autoabertura após boot é etapa opcional: só prometer se a versão do app oferecer suporte e o teste no aparelho comprovar. Macro de Android pode abrir a UI, mas não garante retomada nem contorna o desbloqueio após boot.
Monitor externo pode avisar da ausência do heartbeat; não consegue ressuscitar sozinho o app morto.

## 9. Rede, acesso e backup

- Priorizar automações que fazem conexões de saída. Não é necessário IP público ou redirecionamento de portas.
- Não abrir painel, banco ou SSH na internet. Endpoints locais devem escutar 127.0.0.1 e portas acima de 1024.
- Se precisar de administração remota, avaliar VPN no Android separadamente; só configurar após necessidade explícita e validar roteamento até UserLAnd. Não presumir daemon de VPN dentro de PRoot.
- Webhooks públicos são fase futura: túnel com autenticação/verificação de assinatura, teste externo e endpoint durável; entrega ainda depende de o celular estar online.
- Backup SQLite com API online de backup ou sqlite3 .backup; nunca copiar só o arquivo principal enquanto houver WAL ativo.
- Backup diário com código/configuração e banco, retenção 7 diários + 4 semanais, ajustável ao espaço. Não incluir venv, caches ou tokens por padrão.
- Backups locais não protegem da perda do telefone. Preparar exportação externa, inicialmente manual; sincronização automática só com destino e credenciais fornecidos pelo proprietário.
- Testar restauração em pasta temporária; não sobrescrever produção durante o teste.

## 10. Validação e critérios de aceite

Executar testes curtos durante implantação e deixar comandos para os testes longos. Não declarar 24h validado sem ter passado 24h.

1. Start duas vezes: apenas um supervisor, worker e agenda.
2. Rodar tarefa demo, consultar banco e confirmar resultado persistido.
3. Encerrar apenas o worker e comprovar restart, sem duplicar execução já concluída.
4. Simular timeout/HTTP 429/500 em stub local e verificar backoff, limite de tentativas e logs sanitizados.
5. Simular queda durante execução e verificar a política de recuperação e de efeito externo ambíguo.
6. Fazer backup online e restaurar em pasta temporária; conferir integridade e contagens.
7. Com usuário: tela apagada por 30–60 minutos, depois 8h e 24h; comparar agenda, lacunas e consumo.
8. Com usuário: Wi-Fi desligado por 5 minutos e religado, sem avalanche de tarefas atrasadas.
9. Com usuário: fechar/reabrir sessão e reiniciar aparelho; registrar o tempo de retomada e passos manuais.
10. Medir RSS dos processos, armazenamento/logs e carga durante uso conjunto com Claude Code; reduzir carga se necessário.

Metas iniciais de engenharia, ajustáveis após medir: RSS do conjunto de automações abaixo de 500 MB, sem incluir Android/UserLAnd/Claude; em rede normal, tarefas de 5 minutos iniciando com atraso inferior a 60 segundos durante o piloto; nenhum efeito duplicado na demo. São metas, não garantias.
Registrar em VALIDACAO.md: teste, data, resultado, evidência e pendência. Doze, boot e bateria exigem aparelho real.

## 11. Instalar Claude Code no UserLAnd

Executar no terminal Linux, como usuário normal. Primeiro confirmar distro suportada, aarch64/64 bits, glibc e recursos disponíveis.
Instalador nativo oficial é a primeira opção; não exige instalar Node para o worker Python.

```bash
curl -fsSL https://claude.ai/install.sh -o /tmp/claude-install-estiven.sh
bash /tmp/claude-install-estiven.sh
export PATH="$HOME/.local/bin:$PATH"
claude --version
claude doctor
```

Consultar/inspecionar o instalador antes de executar se desejar; interromper em erro. Persistir PATH em arquivo de perfil adequado sem duplicar linhas.
Depois entrar em ~/estiven4tech-mobile-server e executar claude. Concluir login pela URL apresentada; a assinatura/conta deve ter acesso ao Claude Code, ou usar a modalidade API disponível com cobrança correspondente.
Se houver erro de binário, libc, permissões, syscall ou sandbox, coletar o erro sem segredos. npm não é solução automática: a distribuição npm atual também instala binário nativo. Não desligar todas as proteções nem usar --dangerously-skip-permissions como solução geral.
Se Claude não funcionar no PRoot, o runtime Python ainda pode funcionar: implementar por outra máquina e transferir os arquivos. Registrar isso como alternativa, sem mudar a plataforma solicitada.

Usar nome AGENTS.md em maiúsculas. Para compatibilidade com versões/configurações diferentes do Claude, criar CLAUDE.md ao lado contendo a linha @AGENTS.md. Se CLAUDE.md existir, acrescentar a importação sem apagar conteúdo.
Dentro do Claude, usar /context ou /memory e confirmar que as instruções foram carregadas.

## 12. Primeiro prompt para execução

Copiar para o Claude Code aberto dentro da pasta do projeto:

> Leia integralmente AGENTS.md e implemente este projeto no meu Galaxy S10 Plus com UserLAnd. Comece pelo diagnóstico real e registre o ambiente. Depois crie a base Python + SQLite + Supervisor, scripts idempotentes de operação, tarefa local demonstrativa, backup/restauração e documentação. Execute e valide tudo que puder no terminal. Não use Docker, systemd ou ferramentas exclusivas do Termux. Preserve arquivos existentes e não ative envios ou integrações externas sem credenciais e autorização. Separe as verificações manuais do Android e os testes de 8h/24h, sem afirmar que passaram antes de executar. Ao terminar, entregue os comandos de iniciar, parar, ver status, logs e recuperar após reinício. O risco de interrupção pelo Android já foi aceito; prossiga com as etapas locais autorizadas.

## 13. Migração futura

Usar caminhos configuráveis, dependências fixadas e integrações por variáveis. Para migrar: parar worker, backup consistente, copiar código e banco para host de destino, recriar venv, restaurar, testar e ativar somente uma instância. Nunca manter celular e VPS executando os mesmos efeitos externos sem coordenação.
Manter este host como laboratório/automação pessoal até medir estabilidade. O escopo inicial não inclui migrar clientes nem a produção existente do CRM4tech.

## 14. Referências oficiais consultadas

- UserLAnd: https://github.com/CypherpunkArmory/UserLAnd
- Sessões e configuração: https://github.com/CypherpunkArmory/UserLAnd/wiki/Getting-Started-in-UserLAnd
- Android/Doze: https://developer.android.com/training/monitoring-device-state/doze-standby
- Claude Code/requisitos e instalação: https://code.claude.com/docs/en/setup
- Arquivos de instruções: https://code.claude.com/docs/en/memory

Consultar requisitos vigentes de cada dependência no momento da execução. Compatibilidade no S10 Plus permanece pendente até os testes no terminal real.
