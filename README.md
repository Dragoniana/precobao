# Preço Bão — Monorepo

Plataforma de coleta e comparação de preços de medicamentos em farmácias da região. O repositório é um monorepo com backend em Python (FastAPI), frontend em React Native (Expo) e scripts de bootstrap para configuração de ambiente.

## Visão geral do fluxo

1. **ETL CMED**: a planilha oficial da ANVISA é convertida para CSV e ingerida no PostgreSQL, criando o catálogo base de medicamentos.
2. **Scraping**: módulos em `backend/app/scrapers/` coletam preços e disponibilidade nas farmácias alvo.
3. **Enriquecimento**: serviços em `backend/app/services/` cruzam dados, calculam distâncias e mantêm imagens verificadas.
4. **API**: o FastAPI expõe catálogo, ofertas, histórico de preços e favoritos via endpoints REST.
5. **Frontend**: o app Expo consome a API e apresenta busca, filtros, detalhes, histórico e favoritos.

## Comandos Make (raiz)

Todos os comandos são executados na raiz do repositório.

| Comando | Descrição |
|---|---|
| `make help` | Lista todos os comandos disponíveis |
| `make setup` | Configura o projeto inteiro: cria `.env`, sobe banco, instala dependências, roda migrações |
| `make envs` | Copia `.env.example` para `.env` em backend e frontend |
| `make db-up` | Sobe o PostgreSQL via Docker Compose |
| `make db-down` | Derruba contêineres e destrói volumes persistentes |
| `make db-reset` | Reseta o banco do zero (destrói, sobe e aplica migrações) |
| `make setup-back` | Instala dependências Python com Poetry |
| `make migrate-back` | Aplica migrações Alembic no banco |
| `make setup-front` | Instala dependências Node.js do Expo |
| `make run-back` | Inicia o FastAPI em modo reload |
| `make run-front` | Inicia o app Expo |
| `make etl-convert` | Converte planilha CMED XLSX para CSV |
| `make etl-ingest` | Ingesta o CSV da CMED no PostgreSQL |
| `make etl-run` | Roda o pipeline ETL completo (conversão + ingestão) |

### Variáveis do ETL

Os caminhos dos arquivos CMED podem ser sobrescritos no terminal:

```sh
make etl-run CMED_XLSX=data/nova_planilha.xlsx CMED_CSV=data/cmed_atual.csv
```

## Estrutura do repositório

| Diretório | Conteúdo |
|---|---|
| [`backend/`](backend/README.md) | API FastAPI, scrapers, modelos, schemas, serviços, scripts ETL e testes |
| [`frontend/`](frontend/README.md) | App React Native com Expo Router, componentes, hooks e serviços |
| [`bootstrap/`](bootstrap/README.md) | Scripts de provisionamento de ambiente (Linux e Windows) |

## Infraestrutura

O `docker-compose.yml` do backend define quatro serviços:

| Serviço | Descrição |
|---|---|
| `db` | PostgreSQL 15 Alpine. Porta `4242` (host) → `5432` (contêiner). Volume persistente `postgres_data`. |
| `api` | FastAPI na porta `8000`. Depende de `db` saudável. |
| `image-maintenance` | Ciclo contínuo de verificação e recuperação de imagens. Perfil `maintenance` (não inicia por padrão). |
| `tunnel` | Cloudflare Tunnel (`cloudflared`) expondo a API publicamente. Útil para testar o app mobile em dispositivos físicos fora da rede local sem configurar IP público ou DNS. |

A porta `4242` do PostgreSQL é exposta no host para permitir conexões diretas de clientes SQL externos (DBeaver, psql, etc.) durante o desenvolvimento.

## Pré-requisitos

- Python 3.11+ com Poetry
- Node.js 18+ com npm
- Docker e Docker Compose
- Git

Para instalar tudo automaticamente em uma máquina nova, use os scripts em [`bootstrap/`](bootstrap/README.md).

## Configuração rápida

```sh
make setup
```

Este comando executa toda a sequência: cria arquivos `.env`, sobe o PostgreSQL, instala dependências do backend e frontend, e aplica migrações. Após a conclusão:

```sh
make run-back   # API em http://localhost:8000
make run-front  # Expo em http://localhost:8081
```

## Segurança

- Nunca commite arquivos `.env`. Eles são gerados localmente por `make envs`.
- `JWT_SECRET` deve ser um valor aleatório longo e estável. Trocar invalida tokens existentes.
- `BACKEND_CORS_ORIGINS` aceita lista JSON de origens permitidas. Em desenvolvimento, o regex já libera localhost em qualquer porta.
- Senhas de banco devem usar percent-encoding se contiverem caracteres especiais nos URLs de conexão.

## Documentação por diretório

Cada diretório principal possui seu próprio README com detalhes específicos:

- [Backend](backend/README.md) — arquitetura, endpoints, scripts, modelos, testes e migrações
- [Frontend](frontend/README.md) — estrutura do app, hooks, componentes, comandos e endpoints consumidos
- [Frontend src](frontend/src/README.md) — referência detalhada de hooks, componentes, serviços e tipos
- [Bootstrap](bootstrap/README.md) — scripts de provisionamento e solução de problemas

## Preparação para implantação na Azure

A origem oficial para Azure é a branch `main` deste repositório. Consulte o
[runbook Azure](docs/azure-deployment.md) para VM + Caddy, PostgreSQL gerenciado,
Expo Web/Static Web Apps, backup e restauração, e o
[relatório de prontidão](docs/azure-readiness-2026-11-07.md) para o estado da
preparação e das validações locais. A publicação permanece desabilitada até
autorização e configuração explícitas.
