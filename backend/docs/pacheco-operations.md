# Operação da coleta Pacheco

## Estado de validação

A busca ampla foi observada retornando janelas de 50 itens até `_from=2500`;
`_from=2550` retorna HTTP 400. O scraper agora usa a busca ampla até esse limite
e continua pelas rotas públicas de categoria + marca anunciadas em `Brands` na
API de facetas. Os links e contagens de cada marca vêm da própria resposta da
Pacheco; cada partição é paginada pelo total anunciado e SKUs repetidos entre
partições são descartados antes de enfileirar.

Na consulta observada, Medicamentos informa 11.621 itens, enquanto as 100 marcas
expostas somam 11.199. A coleta compara os IDs únicos descobertos com o total da
faceta e registra a diferença como `source_coverage_gap`; não declara cobertura
completa enquanto esses totais não forem reconciliados. As 87 páginas de sitemap
de produto examinadas são do catálogo geral e incluem itens fora de medicamentos,
portanto não são usadas para inferir cobertura farmacêutica.

## Coleta manual

Na pasta `backend`, após iniciar o banco e configurar `backend/.env`:

```powershell
poetry run python -m app.scrapers.pacheco --max-items 30
poetry run python -m app.scrapers.pacheco --max-items 300
poetry run python -m app.scrapers.pacheco
```

O limite é opcional; sem `--max-items` a coleta percorre a busca ampla e as
partições por marca. O status `partial` significa que as partições retornaram,
mas a contagem anunciada pela fonte ainda não foi reconciliada. Checkpoint e lock
ficam em `data/pacheco/`; uma nova execução recomeça a descoberta desde a
primeira página para tolerar mudanças na ordenação da fonte e preservar
idempotência.

## Agendamento Docker

Após os lotes controlados e a conferência da persistência, histórico, imagens e
integridade no banco local, o serviço `pacheco-scheduler` está habilitado no
ambiente local por `backend/.env` com a configuração abaixo:

```dotenv
PACHECO_SCHEDULER_ENABLED=true
PACHECO_INTERVAL_HOURS=24
PACHECO_FAILURE_RETRY_MINUTES=60
PACHECO_REQUEST_DELAY_SECONDS=1
```

O agendador mantém a cadência no checkpoint, reutiliza o mesmo lock da execução
manual e tenta novamente falhas transitórias pelo intervalo de falha. Uma coleta
`partial` segue o intervalo normal, sem retry frequente; esse estado nunca é
apresentado como cobertura integral. A última varredura local ficou `partial`
porque a fonte anunciou 11.621 itens e foram enumerados 11.144 IDs distintos;
o serviço não contorna essa lacuna e voltará a consultar a fonte na próxima
execução diária.

## Recuperação controlada de HTTP 403

Por padrão, a recuperação automática de 403 permanece desligada. Depois de
confirmar autorização e a liberação técnica da origem, habilite explicitamente
`PACHECO_403_RECOVERY_ENABLED=true`. O agendador registra cada falha no mesmo
checkpoint, espera pelo menos 300 segundos e permite no máximo três execuções
por episódio. Se as três receberem 403, o circuito abre e exige revisão
operacional. Para ajustar o intervalo, `PACHECO_403_RETRY_INTERVAL_SECONDS`
aceita apenas valores iguais ou superiores a 300; `PACHECO_403_MAX_ATTEMPTS`
aceita de 1 a 3. O agendamento geral continua dependendo separadamente de
`PACHECO_SCHEDULER_ENABLED=true`.

Cada execução que recebe 403 para sem avançar a página/offset. A execução
seguinte repete a descoberta desde o início, conforme `replay_from_first_page`,
e a persistência existente continua idempotente. Após a liberação da origem,
resete o circuito somente depois de revisar o acesso. Execute
`python -m app.scrapers.reset_403_circuit pacheco --confirm` a partir de
`backend`; o comando preserva o progresso e deixa a próxima execução pronta.
Nunca remova ofertas com base em uma resposta 403.
