# Feeds autorizados de farmácias

## Estado atual

Os coletores diretos de Araújo e Pacheco não devem ser ativados na produção sem
autorização expressa e uma origem oficial fornecida pela rede. Da VM, requisições
HTTPS convencionais para as duas origens retornaram HTTP 403. Não foram feitas
tentativas de evasão.

Os termos atuais da Araújo vedam softwares de mineração de dados. Os termos da
DPSP condicionam a reprodução do conteúdo das plataformas a autorização prévia e
expressa. Os formulários públicos de fornecedores são canais de contato comercial;
não documentam uma API/feed de preços. O importador deste documento só deve ser
configurado com arquivos ou APIs cujo uso tenha sido autorizado.

## Formatos aceitos

O módulo `app.scrapers.partner_feed` aceita CSV e JSON. O JSON pode ser uma lista
de produtos ou um objeto contendo uma lista em `products`, `items`, `records` ou
`data`. Cada registro deve fornecer identificador, SKU, nome, preço em BRL,
disponibilidade explícita, quantidade em estoque e URL HTTPS oficial. EAN e imagem
são opcionais. Cada registro também exige o horário da observação da fonte em
formato ISO 8601 com fuso (`price_observed_at`, `observed_at` ou
`fonte_observado_em`); por padrão, observações com mais de 24 horas são rejeitadas.
O documento inteiro é parseado e validado antes de a primeira linha ser
enfileirada para persistência. Falhas não removem ofertas já armazenadas.

Campos aceitos incluem `id`/`product_id`, `sku`/`sku_interno`, `ean`/`gtin`,
`name`/`product_name`, `price`/`preco`, `currency`/`moeda`, `available`/`disponivel`,
`quantity`/`stock`, `url`/`product_url` e `image_url`/`imagem_url`.

## Arquivo CSV/JSON

Exemplo de configuração para uma amostra recebida legitimamente:

```dotenv
ARAUJO_PARTNER_FEED_FILE=/secure/feeds/araujo.json
ARAUJO_PARTNER_FEED_FORMAT=json
PACHECO_PARTNER_FEED_FILE=/secure/feeds/pacheco.csv
PACHECO_PARTNER_FEED_FORMAT=csv
```

Execute manualmente após validar a origem e o arquivo:

```powershell
poetry run python -m app.scrapers.araujo
poetry run python -m app.scrapers.pacheco
```

O Compose Azure inclui um agendador de feed Araújo separado e o agendador
Pacheco existente. Ambos ficam desabilitados por padrão (`ARAUJO_SCHEDULER_ENABLED=false`
e `PACHECO_SCHEDULER_ENABLED=false`). Para agendar um arquivo autorizado, monte
o diretório da fonte somente para leitura em `/run/partner-feeds`, configure a
variável `*_FILE` com um caminho dentro desse diretório e habilite o agendador
somente após validar autorização e contrato dos campos. Cada coletor persiste
checkpoints em volume Docker e impede execuções simultâneas por lock.

O importador faz upsert dos produtos recebidos e não apaga ofertas ausentes do
feed. A autenticação ou permissão de reutilização deve ser confirmada fora do
software.

## API oficial de parceiro

Configure uma URL HTTPS, o formato e, quando exigido pelo contrato, bearer token
ou credenciais Basic. As credenciais devem ser fornecidas pelo ambiente seguro e
nunca devem estar na URL, no Git ou nos logs.

```dotenv
PACHECO_PARTNER_FEED_URL=https://api.example.invalid/v1/catalog
PACHECO_PARTNER_FEED_FORMAT=json
PACHECO_PARTNER_FEED_TOKEN=<secret-injected-by-deployment>
```

O cliente verifica TLS, não segue redirects e não repete 403/429. Uma resposta
429 ou bloqueio deixa o lote atual sem importação parcial; a execução deve ser
retomada conforme o intervalo autorizado pelo provedor.

A consulta direta recorrente ao catálogo Araújo ou à API VTEX Pacheco permanece
desabilitada até existir autorização escrita e a variável explícita da origem
ser ativada (`ARAUJO_PUBLIC_CATALOG_AUTHORIZED=true` ou
`PACHECO_PUBLIC_API_AUTHORIZED=true`). Essas variáveis não concedem autorização.
Os trabalhos agendados de preço e imagem respeitam as mesmas travas, evitando
repetir chamadas aos endpoints depois de uma recusa.

As configurações de feed Araújo usam o prefixo `ARAUJO_PARTNER_FEED_` e as de
Pacheco usam `PACHECO_PARTNER_FEED_`. O agendador apenas executa o importador
quando estiver explicitamente habilitado e um arquivo ou endpoint estiver
configurado.
