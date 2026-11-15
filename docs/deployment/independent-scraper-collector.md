# Coletor independente Araújo/Pacheco

## Limite operacional

O coletor faz requisições diretas às farmácias no host onde o processo é executado. Ele não encaminha requisições originadas pela Azure e não tenta contornar recusas. A homologação anterior da Azure retornou HTTP 403 em ambas as origens; portanto, a coleta na Azure permanece desativada.

Antes de habilitar coleta automática, confirme que a autorização cobre o ambiente de execução escolhido, o endereço de saída desse host, a frequência e os limites. O usuário confirmou desenvolvimento/local para esta homologação. O programa exige `SCRAPER_COLLECTION_ENV_AUTHORIZED=true` além dos controles de acesso por fonte. O padrão é desativado.

## Coleta e checkpoints

Inicie com `python -m app.scrapers.scraper_collector_scheduler`. O scheduler usa `America/Sao_Paulo`, por padrão 08:00 e 20:00, e não executa enquanto `SCRAPER_COLLECTOR_ENABLED` for falso. Os coletores reutilizam os parsers existentes, os locks por farmácia e os checkpoints de origem. Eles processam e sincronizam uma página por vez; a fila local não acumula o catálogo inteiro.

Os checkpoints e o outbox são gravados atomicamente em `SCRAPER_COLLECTOR_STATE_DIR`. Se a resposta da API se perder depois da ingestão, o mesmo lote e o mesmo `batch_id` são repetidos do outbox. A API responde idempotentemente ao lote já concluído. Um checkpoint `ACCESS_BLOCKED` impede futuras coletas automáticas dessa fonte até revisão e reset manual. 403 da API de ingestão marca `SYNC_BLOCKED`; o coletor não volta a consultar a farmácia enquanto esse bloqueio não for revisado.

Ao retomar uma coleta interrompida, os parsers repetem a descoberta desde a primeira página. Os lotes já importados são seguros para replay; um outbox pendente é reenviado antes de qualquer nova chamada à farmácia.

## Ingestão autenticada

O endpoint é `POST /api/v1/internal/scraper-batches` (na URL pública atual, prefixe o caminho com `/backend`). O coletor exige URL HTTPS, usa HTTPX com verificação TLS e `trust_env=False`, não segue redirecionamentos e assina o corpo exato com HMAC-SHA256 e timestamp de curta duração. Em staging com CA privada, `SCRAPER_INGEST_CA_BUNDLE` aponta para o bundle confiável sem desativar a validação. A API verifica a assinatura, HTTPS, janela temporal, origem, SKU, tamanho e schema antes de processar.

Use uma chave aleatória exclusiva de pelo menos 32 bytes, criada e armazenada num gerenciador de segredos. Configure o mesmo valor em `SCRAPER_INGEST_HMAC_SECRET` somente no host do coletor e no container da API de staging. Configure `SCRAPER_INGEST_ALLOWED_SOURCES` explicitamente no servidor; por padrão nenhum coletor é aceito. Não use JWT, senha de usuário nem token de sessão. Não grave a chave neste repositório. O limite padrão é 100 produtos, 1 MB por lote e 120 lotes por hora; a API responde `Retry-After` ao excedente. Credenciais de feeds não são utilizadas.

O modelo de auditoria guarda `batch_id`, origem, hash do corpo, estado, contadores e tempos, sem armazenar a carga ou segredo. A persistência reaproveita a política existente: associa ofertas por URL/SKU/EAN, mantém referências de imagem e histórico de preço somente para mudanças reais. Timestamps de observação recebidos são preservados.

## Configuração de referência do host coletor

Configure em arquivo local protegido, não versionado:

```env
SCRAPER_COLLECTOR_ENABLED=false
SCRAPER_COLLECTION_ENV_AUTHORIZED=false
SCRAPER_COLLECTOR_STATE_DIR=/var/lib/preco-bao-collector
SCRAPER_COLLECTOR_SCHEDULE_TIMES=08:00,20:00
SCRAPER_COLLECTOR_TIMEZONE=America/Sao_Paulo
SCRAPER_COLLECTOR_SOURCES=araujo,pacheco
ARAUJO_STATE_DIR=/var/lib/preco-bao-collector/araujo
PACHECO_STATE_DIR=/var/lib/preco-bao-collector/pacheco
SCRAPER_INGEST_URL=https://STAGING_HOST/backend/api/v1/internal/scraper-batches
SCRAPER_INGEST_HMAC_SECRET=
ARAUJO_SOURCE_MODE=DISABLED
ARAUJO_HTTP_CLIENT=httpx
ARAUJO_TLS_VERIFY=true
PACHECO_SOURCE_MODE=DISABLED
PACHECO_HTTP_CLIENT=httpx
PACHECO_TLS_VERIFY=true
```

Para homologação limitada, configure cada fonte para `DIRECT`, confirme os controles de acesso e limite páginas/itens. `ARAUJO_COLLECTOR_ALLOW_PAGE_LIMIT=true` permite registrar uma execução explicitamente parcial quando `ARAUJO_MAX_PAGES` é atingido; `PACHECO_COLLECTOR_MAX_ITEMS` limita os itens. `trust_env=False` impede o uso acidental de proxies definidos no ambiente. A integração com a API permanece HTTPS com TLS validado.

## Migração e staging

Aplicar a migration `f81c3a2d9b10` somente no PostgreSQL de staging. Validar no staging o endpoint, repetição do mesmo lote, mudança e preço inalterado, atualização de estoque, referência de imagem e falha/recuperação antes de configurar qualquer tarefa recorrente. Esta branch não contém credenciais de staging; o staging local temporário usa PostgreSQL dedicado.

Não habilite schedulers Araújo/Pacheco da VM Azure bloqueada. Não habilite o coletor externo até a autorização ambiental e o staging serem confirmados.
