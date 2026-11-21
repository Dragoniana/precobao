# Preparação de produção — API de ingestão de scrapers

## Situação e superfície de mudança

O branch `codex/restore-historical-http-clients` é descendente direto de `origin/main`; no início da preparação ele estava três commits à frente e zero atrás. A rota já passa pelo Caddy em `https://precobao.app/backend/api/v1/internal/scraper-batches` porque `handle_path /backend/*` remove o prefixo e encaminha o restante à API. Caddy, frontend, PostgreSQL, Indiana e volumes não precisam de mudança estrutural.

Para o rollout das imagens, atualizar somente os containers `api` e `web`: a API recebe a migration e serve bytes do PostgreSQL, e o frontend prioriza a rota da API. Manter `offer-refresh` e Indiana em execução sem recriá-los; Araújo/Pacheco na Azure continuam desativados. Não usar `--remove-orphans`. Caddy, banco e volumes não precisam de alteração estrutural.

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

1. Confirmar janela, operador, acesso de emergência e configuração real do proxy/DNS. Não copiar `.env.azure` para artefato nem mostrar segredos nos logs.
2. Atualizar o checkout da VM para este commit/branch após aprovação e executar o preflight de `deploy/azure/preflight.sh`.
3. Criar backup consistente local com `deploy/azure/backup.sh`; executar `verify-backup.sh` para checar hashes, GPG, manifest e legibilidade do dump e do registro de imagens. A cópia fora da VM não é requisito para este rollout. Manter os arquivos locais criptografados e com acesso restrito durante a janela aprovada.
4. Confirmar no `.env.azure` a URL/TLS atuais do PostgreSQL e configurar um HMAC exclusivo para o coletor, com allowlist limitada a `araujo,pacheco`. Não mostrar nem copiar valores secretos para artefatos ou logs.
5. Construir e registrar a imagem candidata da API; preparar também a imagem do `web` com a URL pública atual da API. Preservar a imagem/digest anterior de ambos para rollback. Não alterar Caddy nem abrir portas novas.
6. Aplicar, com confirmação literal e backup verificado, as migrations `f81c3a2d9b10` e `9a6d2f81c4b7` via `deploy/azure/migrate.sh`. Verificar `alembic current`, `alembic check`, tabelas, índices e espaço livre antes de prosseguir.
7. Atualizar somente `api` e `web`, usando o projeto e env file existentes e sem `--remove-orphans`. Revisar a configuração resolvida para confirmar que PostgreSQL, Caddy, volumes, Indiana, `offer-refresh` e schedulers Araújo/Pacheco não serão recriados ou reiniciados. A migration roda isoladamente antes da troca da API.
8. Checar `/health/live`, `/health/ready`, logs sem segredo e rotas de upload/leitura. Enviar uma imagem de teste HMAC inválida para confirmar rejeição e, após configuração, usar apenas dados do piloto autorizado.
9. Instalar/atualizar o coletor Windows com agendamento desativado. Após aprovação específica do piloto, coletar até 10 produtos por origem, conferir preços, disponibilidade, vínculos, bytes, deduplicação e renderização no site. Não ampliar o catálogo nem registrar horários recorrentes sem nova aprovação.

O deploy da migration/API/frontend, alteração de `.env.azure`, restart dos containers, configuração real de HMAC, criação de backup de produção e execução do piloto exigem aprovação explícita e ainda não foram executados. Nenhuma cópia externa de backup é exigida nesta etapa.

## Rollback

1. Parar/suspender o coletor local; preservar outbox e logs para replay e diagnóstico.
2. Restaurar a tag/digest anterior da API e da configuração do worker; recriar somente `api` e `offer-refresh` sem dependências e sem `--remove-orphans`. Reverter a variável diária A/P somente se a decisão operacional for voltar ao comportamento anterior; manter o padrão desligado para evitar chamadas Azure indesejadas.
3. Não fazer downgrade da migration no rollback normal. A tabela aditiva é ignorada pela imagem antiga. Só remover a tabela após interromper ingestão e arquivar auditoria, com aprovação para perda desses registros.
4. Restauração do banco de produção é plano de desastre separado, apenas para banco novo aprovado; não restaurar dump sobre a base ativa. Validar API, Caddy, ofertas e volume de imagens antes de qualquer troca de tráfego.

## Homologação da release candidata PR #5 — 2026-11-21

O branch `codex/scraper-integration-20261120`, commit `3f46275`, foi restaurado na VM em um projeto Docker independente, com rede interna, banco PostgreSQL 15.19, API candidata e Caddy com CA de teste. A porta HTTPS do staging ficou ligada somente a `127.0.0.1`; a rede do projeto não tem rota externa. O banco de produção não foi usado para escrita.

O backup atualizado foi criado como `/var/backups/preco-bao/preco-bao-20261121T043258Z`: dump PostgreSQL e registro de referências de imagens cifrados, hashes SHA-256 e manifesto. A validação estrutural passou e o dump foi restaurado por `pg_restore` 15.19. Antes das migrations, as contagens foram 12.779 itens de catálogo, 15.128 ofertas, 18.041 históricos, 3 farmácias, 13 usuários e 28 favoritos. O Alembic estava em `d30c5f9b24a1`; após as migrations, chegou ao único head `b7e1c4a9d602`. `alembic check` não encontrou drift. A aplicação de migration preservou as contagens; os testes de ingestão seguintes acrescentaram somente dados sintéticos.

O teste [staging_https_smoke.py](../../deploy/azure/staging_https_smoke.py) exige confirmação explícita, endpoint `staging.precobao.test` e banco `restore-db/preco_bao_staging`; recusa outras origens. No staging real, o cliente verificou a cadeia CA interna sem desligar TLS, enviou lotes HMAC das duas origens, rejeitou assinatura inválida, armazenou imagens em BYTEA, deduplicou duas imagens idênticas em um blob, leu a imagem com ETag e recebeu 304 condicional. A recuperação de timeout foi exercitada com transporte HTTP simulado. Os quatro marcadores de outbox foram mantidos, a API de staging foi reiniciada e o replay dos lotes/imagens retornou duplicatas idempotentes.

Os scripts de backup e restauração agora usam a imagem de cliente PostgreSQL configurável e versionada, padrão `postgres:15.19-alpine`, com conferência de versão maior entre cliente e servidor. Credenciais libpq temporárias são armazenadas em arquivos root-only e removidas ao sair. O backup automatiza a validação de hash, descriptografia, leitura do archive, esquema do registro de imagens e manifesto. Restauração com `pg_restore` 15 não emite `SET transaction_timeout` do PostgreSQL 17.

### Compatibilidade com a release ativa

Na VM, a Web está na imagem identificada pela release `e6c42a4`, descendente da correção mobile `84c1aa2` e das alterações recentes de filtro e recuperação de senha. Desde `e6c42a4`, o diff Web do PR #5 contém somente suporte para priorizar imagens armazenadas no PostgreSQL e seus tipos/testes. A Web não é necessária para habilitar ingestão HMAC; o rollout inicial de infraestrutura pode atualizar a API, mantendo a Web sem reinício. Para exibir as imagens BYTEA no site, atualizar a Web em etapa separada.

O PR #5 continua rascunho e não foi integrado. Ele mostra 94 arquivos contra `main` porque a branch de `main` está atrás de funcionalidades que já constam da release implantada; a comparação do deploy precisa usar o digest real da VM. Atualizar a API candidata exige a cadeia `f81c3a2d9b10`, `9a6d2f81c4b7` e `b7e1c4a9d602`. Os schedulers Araújo/Pacheco permanecem desativados. Os ciclos de 403 são somente simulados até a condição de acesso às origens ser esclarecida.

Nenhum container, configuração ou dado de produção foi modificado durante esta homologação. O dump cifrado atual foi verificado e restaurado no ambiente isolado; cópias de backup anteriores foram preservadas.
