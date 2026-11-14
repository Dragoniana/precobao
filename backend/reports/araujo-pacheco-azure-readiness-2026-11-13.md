# Araújo e Pacheco — prontidão Azure

Data: 2026-11-13 (America/Sao_Paulo)
Repositório local: `C:\dev\precobao`, branch `main`, HEAD `ab07d26`
VM: `vm-precobao-api`, Ubuntu 24.04.5 LTS

## Conclusão

As duas fontes não foram reativadas: não há autorização de uso nem contrato de
API/feed para coleta recorrente comprovado. Os 403 previamente observados são
recusas da origem e não há evidência de erro local corrigível que as explique.
Não foram repetidas requisições às farmácias nem usados mecanismos de evasão.

O catálogo Indiana e os serviços atuais da Azure permaneciam saudáveis na
verificação. Os ajustes desta etapa estão no checkout local; nenhum arquivo ou
container da produção foi alterado.

## Diferenças entre desenvolvimento e produção

- Desenvolvimento local: Windows, Python 3.12.10.
- API na VM: Linux em Docker, Python 3.11.18; a VM host usa Python 3.12.3.
- No levantamento anterior, HTTPX, curl-cffi, BeautifulSoup e SQLAlchemy tinham
  versões iguais no ambiente local e no container. Os arquivos `araujo.py`,
  `pacheco.py` e `base.py` também tinham hashes correspondentes entre checkout e
  container.
- Requisições HTTPS convencionais da VM para a listagem Araújo e para o endpoint
  público VTEX Pacheco retornaram HTTP 403 com `text/html`. O relatório anterior
  não preservou cabeçalhos nem corpo completos; a regra exata de recusa é
  indeterminada. Esta execução não repetiu essas chamadas.
- A aplicação original de desenvolvimento usava imitação de navegador; o
  scraper Araújo ainda desligava a verificação TLS. Essa técnica não é uma
  correção legítima para uma recusa da origem e foi removida do caminho ativo.

## Estado observado na produção

Consulta somente de leitura por SSH em 2026-11-14 UTC:

- Checkout da VM: branch `main`, commit `ab07d26`, com alterações locais em
  `offer_refresh_scheduler.py` e `deploy/azure/docker-compose.yml` preservadas.
- `https://precobao.app`: HTTP 200.
- Endpoint HTTPS público `/health/ready`: HTTP 200.
- API: container `healthy`; Caddy, frontend e worker Indiana ativos.
- Configuração observada: `OFFER_REFRESH_ENABLED=true`,
  `PACHECO_SCHEDULER_ENABLED=false`; os logs do worker mostram Indiana ligada e
  Araújo/Pacheco desligadas. Portanto, não há coleta recorrente dessas duas
  fontes na configuração observada.
- A atualização Indiana concluiu uma rodada de imagens sem indisponibilidades;
  uma rodada de preços registrou 8 eventos, 237 ofertas sem alteração e 4
  divergências de identidade/EAN. Isso é evidência de execução, não cobertura
  completa do catálogo.
- Recursos: 3,8 GiB RAM total, 2,6 GiB disponíveis, swap sem uso; disco raiz de
  29 GiB com 30% usado.
- Nenhuma consulta ou escrita no PostgreSQL foi feita nesta etapa. A verificação
  de prontidão e os workers existentes não foram interrompidos.

## Alterações locais preparadas

- Araújo conserva o parser HTML para uso offline e utiliza o importador comum
  apenas quando recebe um feed configurado. Foi adicionado agendador Araújo com
  checkpoint persistente, lock entre processos, logs JSON, intervalo/retry
  configuráveis e ativação desabilitada por padrão.
- Pacheco conserva o parser VTEX e seus testes de identidade, preço, estoque e
  imagem. A integração por feed autorizado é preferida; a API pública só pode
  ser consultada com autorização explicitamente configurada.
- Os caminhos recorrentes de preço e imagem param antes de conectar às fontes
  Araújo/Pacheco sem autorização. Após 403, não repetem; após 429, respeitam
  `Retry-After` quando fornecido.
- Feeds CSV/JSON e APIs HTTPS autenticadas exigem moeda BRL, disponibilidade,
  estoque, preço positivo, URL HTTPS, timestamp com fuso e observação recente.
  O lote inteiro é validado antes de enfileirar e não remove ofertas existentes.
- O Compose inclui o worker Araújo e repassa configurações de feed. Ambos os
  agendadores de catálogo ficam desabilitados por padrão; a Indiana mantém o
  serviço atual.
- Não foi criada migração de banco nem alterado dado de produção.

## Testes locais

- Ruff: aprovado.
- Backend: 119 testes aprovados.
- Compose Azure: sintaxe válida.
- `git diff --check`: aprovado.
- Os testes cobrem parser offline Araújo, extração VTEX Pacheco, validação de
  CSV/JSON/API, identidade, disponibilidade, falhas 403/429, checkpoints,
  locks, agendamento e bloqueios sem autorização. Nenhum teste consulta uma
  farmácia real ou banco de produção.

## Acesso que falta para ligar cada fonte

**Araújo:** os termos atuais vedam software de mineração de dados no site. É
necessária permissão escrita específica para coleta recorrente, armazenamento e
exibição de preços/imagens, além de um feed/API oficial e sua documentação. O
cadastro público de fornecedores é apenas um canal de contato.

**Pacheco/DPSP:** os termos condicionam a reprodução do conteúdo a autorização
prévia e expressa. É necessária permissão para usar preço, catálogo e imagens e
um endpoint/feed oficial documentado com autenticação e limites aprovados.

Para ambos, solicitar campos de identificador/SKU, EAN, nome e apresentação,
preço e moeda, condições comerciais, disponibilidade/estoque, seller, URL e
licença de imagem, timestamp, contexto de venda/CEP quando aplicável, cadência,
limites, cache/retensão, SLA e amostra pequena de homologação. O rascunho de
solicitação está em `backend/docs/partner-integration-request.md`.

## Publicação

Na verificação registrada acima, nenhuma publicação ou implantação foi feita.
A conexão SSH foi somente de leitura; não houve backup porque nenhum deploy foi
iniciado. O código desta revisão pode ser versionado localmente, mas só deve ser
enviado ao GitHub após aprovação. Antes da ativação de Araújo/Pacheco, também é
necessária autorização formal das fontes.
