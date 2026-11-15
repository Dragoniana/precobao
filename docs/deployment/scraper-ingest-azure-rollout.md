# Preparação de produção — API de ingestão de scrapers

## Situação e superfície de mudança

O branch `codex/restore-historical-http-clients` é descendente direto de `origin/main`; no início da preparação ele estava três commits à frente e zero atrás. A rota já passa pelo Caddy em `https://precobao.app/backend/api/v1/internal/scraper-batches` porque `handle_path /backend/*` remove o prefixo e encaminha o restante à API. Caddy, frontend, PostgreSQL, Indiana e volumes não precisam de mudança estrutural.

Para a futura ativação do coletor local, atualizar somente a imagem/container `api` e o worker `offer-refresh` (mantém Indiana, mas deixa `OFFER_REFRESH_DAILY_SOURCES` vazio para Araújo/Pacheco não fazerem coleta direta na VM). Os serviços legados `araujo-scheduler` e `pacheco-scheduler` ficam desativados. Não usar `--remove-orphans`. Não recriar banco, web/Caddy ou containers Indiana. O `offer-refresh` permanece ligado com `OFFER_REFRESH_SOURCES=indiana` e `OFFER_IMAGE_REFRESH_SOURCES=indiana`.

O Compose recebeu uma tag explícita de imagem para a API (`API_IMAGE`, padrão `preco-bao-api:latest`) e o padrão seguro para a coleta diária Araújo/Pacheco é vazio. Isso só muda a configuração versionada; nenhum arquivo `.env.azure` nem container de produção foi alterado.

## Migration e impacto

A `f81c3a2d9b10` adiciona apenas `scraper_import_batches`: chave primária `batch_id`, estado/contadores/hash, tempos e dois índices (`source`, `status`). Não altera nem reescreve catálogo, oferta, histórico, usuários ou favoritos. O rollback dessa migration remove a tabela de auditoria e seus índices, portanto descarte seus dados; para rollback normal de aplicação, mantenha a tabela e volte somente a imagem.

Validação descartável concluída em PostgreSQL 15, igual ao `postgres:15-alpine` declarado na configuração de produção:

- `alembic upgrade head` e `alembic check`: aprovados, sem drift.
- `alembic downgrade -1` seguido de `upgrade head` em banco de rollback separado: aprovado.
- Índices da auditoria e chave primária verificados no catálogo PostgreSQL.
- Dump custom-format restaurado, em transação, para outro banco vazio: contagens conferidas.
- Dados exclusivamente sintéticos, sem usuário, credenciais ou dumps pessoais.

## Carga e comportamento da ingestão

O staging foi semeado com 12.747 registros de catálogo, 15.049 ofertas, 15.049 eventos de histórico e três farmácias sintéticas. Dois lotes de 11 produtos (dez ofertas existentes para atualização e uma nova oferta em cada lote) exercitaram associação por EAN/SKU/URL, inserção, atualização, referência de imagem e auditoria. As verificações confirmaram contexto `loja`/canal/loja_id preservado, histórico apenas para cinco preços alterados por origem, produto ausente do lote sem alteração de disponibilidade, e 22 referências de imagem registradas.

O mesmo `batch_id`/corpo foi repetido e retornou duplicado sem regravar. Um registro `PROCESSING` antigo foi criado para simular queda após claim; o replay completou a tentativa sem duplicar oferta ou histórico. Um lote adicional com preços inalterados não criou evento de mudança. Totais finais: 12.749 itens de catálogo, 15.051 ofertas, 15.061 eventos de histórico, três farmácias. O dump de staging restaurado preservou esses totais. As imagens foram validadas quanto à URL HTTPS, host permitido, extensão e associação da origem; a ingestão não baixa bytes nem atesta MIME/conteúdo, que permanecem sob o validador/monitor de imagem existente.

O limite de amostra não faz inferência de indisponibilidade para produtos não enviados: a API só altera as ofertas que aparecem no lote. O coletor não recebe acesso ao banco e os processos não usam proxy para buscar páginas de farmácias.

## Segurança e credenciais

- HMAC exclusivo e segredo mínimo de 32 bytes; usar o gerenciador de segredos do host/API. O segredo não está no Git. Não usar JWT nem credencial de usuário.
- API com allowlist explícita `araujo,pacheco`; limite atual: 100 produtos/lote, 1 MB/corpo e 120 chamadas/hora, janela HMAC de 5 minutos e limite lido durante o streaming.
- Reenvio idempotente por `batch_id` e hash de corpo; payload divergente com mesmo ID resulta em conflito. Auditoria guarda hash/contadores/estado e não o corpo nem o segredo.
- API acessível por HTTPS via Caddy; não abrir porta do PostgreSQL nem mudar TLS. Cliente local mantém verificação TLS e não aceita proxy via ambiente.
- Imagens aceitas apenas por HTTPS, host/origem/arquivo permitido e associação à farmácia declarada. O fetch de conteúdo continua segregado no sistema de imagens.
- Rotação preparada no cliente Windows com DPAPI: suspender tarefas, trocar primeiro a credencial da API por canal protegido, atualizar o segredo local, verificar ingestão e reabilitar. Se a chave divergir, os lotes ficam no outbox.
- Risco residual: o rate limiter existente é em memória por processo; com múltiplas réplicas, a quota não é global. Para rollout inicial, manter uma instância API e revisar antes de escalar horizontalmente.

## Instalação no Windows

Documentação e scripts em [deploy/windows-scraper-collector](../../deploy/windows-scraper-collector/README.md). Requer Python 3.12, PowerShell, acesso ao repositório e usuário Windows dedicado. O instalador cria dependências/configuração sob `%LOCALAPPDATA%\PrecoBaoCollector`, trava a ACL e armazena HMAC em DPAPI. Configuração inicial: `api_ready=false`, `collector_enabled=false`, fontes `DISABLED`; nenhum agendamento é criado.

Após deploy e homologação aprovados, configurar URL HTTPS e fonte em `DIRECT`, gravar o mesmo HMAC com `Set-CollectorSecret.ps1`, rodar Araújo/Pacheco individualmente em amostra e, então, ambas. `Status-Collector.ps1` mostra checkpoints, outbox, estado e log. `Stop-Collector.ps1` desativa tarefas e solicita parada após sincronizar página atual. Agendamento 08:00/20:00 BRT só é registrado com `Register-Schedule.ps1 -Enable` e configuração explicitamente pronta. Recuperação após retorno do computador coalescida em uma execução até 120 minutos; acima disso, loga horário perdido e aguarda próxima janela.

## Procedimento proposto para aprovação de deploy

1. Confirmar janela, operador, acesso de emergência, configuração real do proxy/DNS e backup GPG fora da VM. Não copiar `.env.azure` para artefato nem mostrar segredos nos logs.
2. Atualizar o checkout da VM para este commit/branch após aprovação e executar o preflight de `deploy/azure/preflight.sh`.
3. Fazer backup com `deploy/azure/backup.sh`; validar SHA/GPG por `verify-backup.sh` e testar restauração em servidor/banco gerenciado separado e vazio. Confirmar também o backup restaurável do volume `image_references`.
4. Confirmar em `.env.azure` `SCRAPER_INGEST_HMAC_SECRET` exclusivo e `SCRAPER_INGEST_ALLOWED_SOURCES=araujo,pacheco`; manter TLS/URL do PostgreSQL atual. Definir `OFFER_REFRESH_DAILY_SOURCES=` para parar a coleta direta A/P no worker Azure, mantendo Indiana.
5. Construir a imagem API; usar `API_IMAGE=preco-bao-api:<release>` como tag aprovada e registrar o digest. A imagem candidata local foi criada e seu import/rota foi smoke-tested, mas precisa ser reconstruída/identificada no ambiente de release.
6. Aplicar migration usando `deploy/azure/migrate.sh`, que exige confirmação literal após backup. Verificar `alembic current` e `alembic check`.
7. Recriar apenas `api` e `offer-refresh` com o nome do projeto e env file existentes, em comandos explícitos, sem `--remove-orphans`. Antes do apply, revisar `docker compose config --services` e o diff para assegurar que Caddy, web, banco, Indiana, volumes e workers desligados não serão reiniciados.
8. Checar `/health/live`, `/health/ready`, logs sem segredo e rota de ingestão com lote de homologação autorizado. Fazer requests com HMAC inválida, timestamp antigo, source não allowlisted e corpo acima do limite: esperar 401/403/413/422; lote válido deve gerar auditoria `COMPLETE` e replay deve ser `duplicate=true`.
9. Só após validação controlada, habilitar cliente Windows e executar lote pequeno por origem. Conferir contadores, amostra de preço, contexto, imagens e auditoria. Só então obter aprovação separada para registrar tarefa 08/20.

O deploy da migration/API, alteração de `.env.azure`, restart de containers, configuração real de HMAC, criação de backup de produção e ativação do agendamento exigem aprovação explícita e ainda não foram executados.

## Rollback

1. Parar/suspender o coletor local; preservar outbox e logs para replay e diagnóstico.
2. Restaurar a tag/digest anterior da API e da configuração do worker; recriar somente `api` e `offer-refresh` sem dependências e sem `--remove-orphans`. Reverter a variável diária A/P somente se a decisão operacional for voltar ao comportamento anterior; manter o padrão desligado para evitar chamadas Azure indesejadas.
3. Não fazer downgrade da migration no rollback normal. A tabela aditiva é ignorada pela imagem antiga. Só remover a tabela após interromper ingestão e arquivar auditoria, com aprovação para perda desses registros.
4. Restauração do banco de produção é plano de desastre separado, apenas para banco novo aprovado; não restaurar dump sobre a base ativa. Validar API, Caddy, ofertas e volume de imagens antes de qualquer troca de tráfego.
