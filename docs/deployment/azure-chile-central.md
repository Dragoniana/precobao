# Preço Bão — coletores Araújo e Pacheco

## Estado desta integração

Esta branch consolida as alterações que já estavam na produção: a reconciliação
do `offer-refresh` (`1c3435e`) e a otimização das consultas do catálogo
(`81439fd`). Os arquivos locais da VM correspondem a esses commits, exceto a
rota de catálogo, que já recebeu a otimização, e seu teste correspondente.
Nenhum segredo ou dado de produção faz parte desta documentação.

Os modos dos coletores agora são explícitos. O padrão é `DISABLED`; esta mudança
de código não habilita nem reinicia os serviços Araújo ou Pacheco na Azure.
O `offer-refresh` mantém as verificações de fonte e só chama Araújo/Pacheco em
modo `DIRECT`, com autorização e confirmação de acesso explícitas. Indiana
continua independente.

## Seleção da origem

Configure `ARAUJO_SOURCE_MODE` e `PACHECO_SOURCE_MODE` separadamente:

- `DISABLED`: não lê site ou feed. É o padrão seguro.
- `DIRECT`: usa os endpoints públicos permitidos, com TLS verificado e sem
  fallback automático para feed. Exige a variável de autorização da fonte e
  `*_ACCESS_CONFIRMED=true` após validar o acesso técnico.
- `FEED`: usa somente o feed configurado. Ausência ou recusa do feed não muda
  para `DIRECT`.

Para ativar um agendador, configure também seu `*_SCHEDULER_ENABLED=true`.
Araújo usa `ARAUJO_PUBLIC_CATALOG_AUTHORIZED`; Pacheco usa
`PACHECO_PUBLIC_API_AUTHORIZED`. Os dois requerem ainda
`ARAUJO_ACCESS_CONFIRMED` ou `PACHECO_ACCESS_CONFIRMED`, respectivamente. Não
coloque URLs com credenciais ou tokens no Git.

## Coletores diretos

**Araújo:** consulta `https://www.araujo.com.br/medicamentos?page=N` com HTTPX,
TLS verificado, concorrência sequencial e atraso configurável. O parser
`productTile` foi comparado com uma resposta atual: a primeira página continha
48 cards e o parser produziu 48 produtos com SKU, EAN, preço e imagem. O
checkpoint grava a última página lida; após interrupção, a descoberta é
reexecutada desde a primeira página e a persistência idempotente elimina
duplicações. `ARAUJO_REQUEST_DELAY_SECONDS` controla o intervalo e
`ARAUJO_MAX_PAGES` limita a passagem.

**Pacheco:** mantém a busca VTEX, facetas de marcas, paginação de 50 itens,
deduplicação, identidade por SKU/EAN e checkpoint persistente. Não ultrapassa o
limite de paginação conhecido do endpoint geral. O caminho `DIRECT` não carrega
nem exige feed.

Os dois modos `FEED` continuam disponíveis para descoberta/importação
validada. Atualização incremental de ofertas e atualização somente de imagens
continuam como operações separadas no `offer-refresh`; ambas preservam o
histórico e usam cursores próprios.

## Falhas e retomada

- `200` (Araújo) e `200`/`206` (VTEX): processar resposta válida.
- `403`: gravar `ACCESS_BLOCKED`, manter o último checkpoint bom e pausar a
  fonte. Inspecione o checkpoint antes de retomar; só remova o estado de bloqueio
  depois de confirmar com a farmácia que o acesso técnico foi liberado.
- `429`: sem retry imediato; respeitar `Retry-After` e o intervalo do scheduler.
- `5xx` e timeout: retries limitados com backoff; registrar falha e permitir
  retomada idempotente.
- Uma alteração do HTML que deixe a primeira página sem `productTile` é erro de
  parser, não fim bem-sucedido do catálogo.

Os schedulers Araújo/Pacheco e `offer-refresh` precisam de volumes persistentes
de estado. O agendamento de preços em `offer-refresh` usa os horários
configuráveis `OFFER_REFRESH_SCHEDULE_TIMES`, por padrão 08:00 e 20:00 no fuso
`America/Sao_Paulo`; nenhuma fonte nova é incluída nessa lista por esta branch.

## Homologação registrada

Na máquina de desenvolvimento, uma requisição única e sem retry retornou HTTP
200 para a página da Araújo e HTTP 206 para a primeira página VTEX da Pacheco.
Com essas respostas, os parsers produziram 48 e 50 itens, respectivamente. O
container local usa Python 3.12.10 e HTTPX 0.28.1; a imagem Azure usa Python
3.11.18 e a mesma versão HTTPX. Os dois usam TLS verificado. O código histórico
que usava `curl_cffi` com `verify=False` não é usado.

Uma leitura única por origem, feita do container existente `offer-refresh` na
Azure, retornou HTTP 403 para Araújo e Pacheco. Não houve retry nem persistência
no banco. O DNS na Azure e no desenvolvimento resolveu edges CDN diferentes.
Isso localiza a diferença no acesso a partir do ambiente Azure, mas não prova
que o IP de saída seja a causa; os headers de diagnóstico e o corpo de recusa
não foram preservados. A causa exata continua pendente de confirmação das
farmácias. Forneça a elas o horário e caminhos consultados e peça confirmação
do endereço de origem visto nos logs, de eventual allowlist de IP, credenciais
ou exigências específicas para o endpoint. O endereço público conhecido da VM
é `68.211.144.200`, mas não deve ser tratado como causa comprovada.

Até essa confirmação, mantenha os modos `DISABLED`, os schedulers Araújo e
Pacheco desativados e as flags de autorização/acesso falsas na configuração
Azure. Se qualquer fonte responder 403, interrompa-a e solicite formalmente a
liberação ou configuração técnica necessária; não use proxies, falsificação de
identidade ou repetição para contornar a recusa.

## Operação segura

Valide as variáveis sem expor `.env.azure`:

```sh
cd /opt/preco-bao/deploy/azure
sudo ./preflight.sh
sudo docker compose --project-name preco-bao-azure --env-file .env.azure ps
```

The preflight requires `INDIANA_SCRAPER_ENABLED=true` and
`OFFER_REFRESH_ENABLED=true`. Missing values stop Compose interpolation; false
values stop preflight. `SCRAPER_STATE_HOST_DIR` defaults to
`/var/lib/preco-bao/scraper-state`, shared by the Indiana and offer-refresh
checkpoints. Preserve this directory and the named image-reference and Caddy
volumes. Preflight resolves the Compose configuration and checks Caddy routes;
it does not build or start containers. Run `bash test-preflight.sh` to test a
valid fixture and the required failure when Indiana is disabled.

Para acompanhar um serviço:

```sh
sudo docker compose --project-name preco-bao-azure --env-file .env.azure logs --tail=100 araujo-scheduler pacheco-scheduler offer-refresh
```

Não use `--remove-orphans`. Não mude o modo para `DIRECT` nem habilite um
scheduler antes da homologação da origem. Preserve a imagem anterior da API e os
volumes dos schedulers em qualquer implantação; não execute migrations para
ativar os coletores.
