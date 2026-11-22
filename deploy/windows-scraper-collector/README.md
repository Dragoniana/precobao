# Coletor Windows — Araújo e Pacheco

Este pacote executa os coletores existentes no Windows e envia somente os lotes validados à API HTTPS. Não configura acesso ao PostgreSQL. Por padrão, a API e as fontes estão desativadas e nenhum agendamento é criado.

## Instalação e configuração

1. Clone/atualize o repositório no computador autorizado e abra PowerShell no repositório.
2. Execute `Set-ExecutionPolicy -Scope Process Bypass` e depois `.\deploy\windows-scraper-collector\Install-Collector.ps1`.
3. Configure a API já implantada em `%LOCALAPPDATA%\PrecoBaoCollector\settings.json`: `api_ready=true`, `collector_enabled=true`, URL HTTPS da rota e fontes em `DIRECT` com `access_confirmed=true`. Mantenha o fuso do Windows em `America/Sao_Paulo` (UTC-03:00).
4. Guarde o mesmo segredo HMAC exclusivo do coletor/API com `.\deploy\windows-scraper-collector\Set-CollectorSecret.ps1`. O arquivo fica cifrado via DPAPI para o usuário atual e ACL da pasta restrita; o segredo não entra no Git nem nos logs.
5. Antes de cada atualização do repositório, pare o agendamento, atualize a branch e reinstale dependências com `Install-Collector.ps1`.

O estado operacional pertence a uma única cópia do repositório por conta Windows. O instalador interrompe outra instalação com raiz diferente antes de alterar o estado; use uma conta Windows separada para operar instalações independentes.

Após a confirmação de cada lote de produtos, o coletor baixa e normaliza as imagens em série no Windows e as envia em requisições binárias HMAC separadas; o endpoint deriva da URL configurada do lote. O outbox mantém metadados e bytes da imagem até a API confirmar o armazenamento, permitindo retomar um upload sem reenviar ofertas. Falhas de download, timeout ou upload são registradas por SKU com um motivo seguro e não desfazem nem interrompem os lotes de preço já confirmados. Um download da origem é tentado uma vez por produto em cada execução, sem retry em 403/404; falhas de envio mantêm o item no outbox para um ciclo posterior. O estado e o outbox ficam sob a mesma pasta local com ACL restrita. Imagens específicas armazenadas são priorizadas, seguidas dos fallbacks genéricos oficiais e do placeholder interno.

Os checkpoints de coleta são cursores de retomada, não apenas relatórios. Araújo guarda a próxima página e o índice do item dentro dela; ao atingir 50 no meio de uma página, a próxima execução continua nos itens ainda não enviados. Após percorrer todas as páginas, o cursor volta à página inicial para revisitar preços existentes. Pacheco guarda a etapa, partição e próximo offset; ao terminar as partições disponíveis, reinicia um ciclo para revisitar produtos e detectar novos itens. Uma falha antes da confirmação do lote não avança o cursor. A retomada após queda pode repetir a última página confirmada, com sincronização idempotente.

Não habilite as fontes antes da instalação da API/migration aprovada e da homologação final. A API Azure precisa continuar com TLS verificado; o coletor rejeita HTTP e não usa variáveis de proxy (`trust_env=False`).

## Comandos

```powershell
# Instalar dependências e criar configuração segura/desativada
.\deploy\windows-scraper-collector\Install-Collector.ps1

# Coletar apenas uma origem ou as duas, na ordem Araújo/Pacheco
.\deploy\windows-scraper-collector\Invoke-Collector.ps1 -Source araujo
.\deploy\windows-scraper-collector\Invoke-Collector.ps1 -Source pacheco
.\deploy\windows-scraper-collector\Invoke-Collector.ps1 -Source both

# Ver status local, log, outbox e checkpoints
.\deploy\windows-scraper-collector\Status-Collector.ps1

# Reenviar somente imagens que já estão na outbox; não consulta as farmácias
.\deploy\windows-scraper-collector\Sync-Pending-Images.ps1 -Source pacheco
.\deploy\windows-scraper-collector\Sync-Pending-Images.ps1 -Source araujo

# Pedir parada segura (termina a página/lote atual) e suspender tarefas
.\deploy\windows-scraper-collector\Stop-Collector.ps1

# Instalar/remover agendamento após prontidão e aprovação
.\deploy\windows-scraper-collector\Register-Schedule.ps1 -Enable -Source araujo
.\deploy\windows-scraper-collector\Register-Schedule.ps1 -Enable -Source pacheco
.\deploy\windows-scraper-collector\Unregister-Schedule.ps1
```

## Execução e recuperação

O agendamento permanece desativado após a instalação. O teto global é de 50 tentativas HTTP externas em qualquer janela móvel de 900 segundos, compartilhado por Araújo e Pacheco. Catálogo, páginas, detalhes, imagens e tentativas repetidas contam; POSTs HMAC à API Preço Bão não contam nesse orçamento. O rate limiter grava cada reserva no disco, usa trava entre processos e aguarda espaço sem zerar no reinício. 403 suspende a origem por pelo menos uma hora; 429 respeita `Retry-After`. Não há equivalência entre requisições e quantidade de produtos.

Cada origem tem uma tarefa separada no fuso local `America/Sao_Paulo`: Araújo nos minutos 00, 15, 30 e 45; Pacheco nos minutos 07, 22, 37 e 52. Registre Araújo primeiro e habilite Pacheco somente depois de confirmar uma execução agendada da Araújo com lote aceito pela API. `StartWhenAvailable` e a validação persistente dos slots evitam acumular execuções atrasadas; atraso superior a cinco minutos é ignorado. O Agendador ignora uma nova execução da mesma origem enquanto a anterior estiver ativa; as duas origens compartilham o rate limiter. Checkpoints, outboxes, conflitos e orçamento ficam sob `%LOCALAPPDATA%\PrecoBaoCollector\state`. Se a API falhar após coleta, o outbox permanece para reenvio no próximo ciclo.

Em tarefa agendada, quando o orçamento compartilhado está cheio, o cliente não espera pela janela: termina o lote confirmado, guarda imagens ainda não baixadas em `outbox\image-fetch`, deixa o cursor no próximo item ainda não confirmado e encerra com `BUDGET_EXHAUSTED` (código 12). O próximo ciclo retoma primeiro uploads pendentes, depois downloads de imagem adiados e então o catálogo. O estado do rate limiter permanece no disco; encerrar o processo não reinicia a janela. Em execução manual, o modo de espera continua disponível. Cooldowns da origem permanecem distintos: 403 resulta em `ACCESS_BLOCKED` (2) e 429 em `RATE_LIMITED` (5), sem nova chamada durante o cooldown.

`Stop-Collector.ps1` cria um marcador local. A coleta em andamento termina a sincronização da página atual, para antes de buscar a próxima e mantém qualquer outbox pendente; tarefas agendadas ficam suspensas. Uma execução manual/agendada posterior remove o marcador ao iniciar. Logs não registram corpo, HMAC ou credenciais. Se um processo terminar inesperadamente e deixar o estado `RUNNING`, o ciclo seguinte só identifica esse estado como interrompido depois de adquirir a trava exclusiva da origem; mantém checkpoint/outbox e registra o horário da execução anterior. O `Status-Collector.ps1` também mostra `INTERRUPTED (estado salvo)` quando não encontra processo Python ativo.

As tarefas usam logon interativo para que o mesmo usuário Windows possa descriptografar o segredo DPAPI. Elas podem executar com a estação bloqueada enquanto a sessão do usuário continuar conectada. Após sair da conta, não executam; após reinicialização, aguardam o próximo logon e a política `StartWhenAvailable` pode iniciar uma única execução recuperada dentro da janela configurada. Não funcionam sem login, em `SYSTEM`, nem sob outra conta. Cada origem tem trava própria contra sobreposição; o rate limiter mantém a trava compartilhada do orçamento. `Status-Collector.ps1` mostra o resultado mais recente e o código de saída da tarefa: 0 concluída, 2 acesso negado, 3 parcial, 4 parada segura, 5 limite da origem, 6 falha de API/HMAC, 7 erro, 8 origem desativada, 9 lock ocupado, 10 slot perdido, 11 slot duplicado, 12 orçamento externo esgotado e 13 limite de produtos configurado. O limite de requisições é persistido separadamente e mostrado no `Status-Collector.ps1`. Use `Restore-Schedule.ps1` com a pasta de backup salva ao suspender os agendamentos anteriores para reverter a ativação.

## Segredo e rotação

Use segredo aleatório exclusivo com pelo menos 32 bytes; não reutilize JWT ou credenciais de usuários. Para rotação, suspenda as tarefas, atualize o HMAC na API por canal seguro e então rode `Set-CollectorSecret.ps1` com o novo valor antes de reabilitar as tarefas. Faça isso dentro de uma janela curta; segredo inválido deixa o lote preservado no outbox e interrompe a sincronização.
# Migrar imagens já existentes no PostgreSQL de desenvolvimento

`Sync-DevelopmentImages.ps1` lê somente registros de imagens do PostgreSQL de
desenvolvimento e consulta a API HTTPS/HMAC para identificar medicamentos
correspondentes e sem imagem em produção. O padrão é `-DryRun` implícito: a API
somente verifica candidatos e nenhum byte é enviado. O relatório sanitizado fica
em `development-image-migration-report.json` dentro do diretório de estado do
coletor. Configure `PRECOBAO_DEVELOPMENT_DATABASE_URL` no processo chamador sem
colocá-la em argumentos, arquivos versionados ou logs.

Após revisar o relatório e confirmar um backup recuperável da produção, a
execução real exige ambos os parâmetros `-Execute -BackupVerified`. O script
revalida cada medicamento imediatamente antes do upload, envia uma imagem por
medicamento via HTTPS/HMAC e mantém o PostgreSQL de desenvolvimento em transação
somente de leitura. Produção não recebe escrita direta do computador Windows.
Um EAN inválido, associação ambígua, fonte incompatível ou imagem existente em
produção causa uma rejeição/ignoragem sem alterar catálogo, ofertas ou preços.
Reexecutar é seguro: o servidor verifica novamente a imagem e a associação.

Os scrapers removem os arquivos temporários `.bin` e o manifesto somente após a
API confirmar o hash e a associação (ou confirmar uma imagem já existente que
torna o fallback desnecessário). A confirmação é gravada em metadados mínimos
antes da exclusão. Se o processo parar durante a limpeza, a próxima execução a
conclui sem reenviar. Arquivos sem confirmação continuam preservados ou são
mantidos em quarentena. As imagens originais no PostgreSQL de desenvolvimento
nunca são excluídas por essa ferramenta.

O status mostra fila, tamanho dos temporários, órfãos/quarentena, confirmações e
espaço liberado. Nenhum agendamento é criado ou habilitado por esses scripts.
