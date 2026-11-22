# Coletor Windows — Araújo e Pacheco

Este pacote executa os coletores existentes no Windows e envia somente os lotes validados à API HTTPS. Não configura acesso ao PostgreSQL. Por padrão, a API e as fontes estão desativadas e nenhum agendamento é criado.

## Instalação e configuração

1. Clone/atualize o repositório no computador autorizado e abra PowerShell no repositório.
2. Execute `Set-ExecutionPolicy -Scope Process Bypass` e depois `.\deploy\windows-scraper-collector\Install-Collector.ps1`.
3. Configure a API já implantada em `%LOCALAPPDATA%\PrecoBaoCollector\settings.json`: `api_ready=true`, `collector_enabled=true`, URL HTTPS da rota e fontes em `DIRECT` com `access_confirmed=true`. Mantenha `timezone=America/Sao_Paulo` e os horários `08:00,20:00`.
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
.\deploy\windows-scraper-collector\Register-Schedule.ps1 -Enable
.\deploy\windows-scraper-collector\Unregister-Schedule.ps1
```

## Execução e recuperação

O agendamento permanece desativado após a instalação. Para registrá-lo, a configuração exige limite entre 1 e 50 produtos por tarefa; valor ausente ou inválido impede o registro e a execução. Cada farmácia tem tarefas separadas: Araújo às 08:00/20:00 e Pacheco às 10:00/22:00 no fuso `America/Sao_Paulo`, com intervalo para evitar concorrência. Só fontes marcadas `DIRECT` e com acesso confirmado recebem tarefas. `StartWhenAvailable` permite uma única recuperação após retorno do computador; execuções atrasadas até 120 minutos são anotadas e coalescidas numa só execução. Atrasos maiores são anotados como perdidos e ignorados. IDs de slot persistidos impedem disparo duplicado; a trava global impede sobreposição. Checkpoints, outboxes e a fila persistente de conflitos ficam sob `%LOCALAPPDATA%\PrecoBaoCollector\state`. Se a API falhar após coleta, o outbox permanece para reenvio no próximo ciclo.

`Stop-Collector.ps1` cria um marcador local. A coleta em andamento termina a sincronização da página atual, para antes de buscar a próxima e mantém qualquer outbox pendente; tarefas agendadas ficam suspensas. Uma execução manual/agendada posterior remove o marcador ao iniciar. Logs não registram corpo, HMAC ou credenciais.

As tarefas usam logon interativo para que o mesmo usuário Windows possa descriptografar o segredo DPAPI. Elas podem executar com a estação bloqueada enquanto a sessão do usuário continuar conectada. Após sair da conta, não executam; após reinicialização, aguardam o próximo logon e a política `StartWhenAvailable` pode iniciar uma única execução recuperada dentro da janela configurada. Não funcionam sem login, em `SYSTEM`, nem sob outra conta. Os slots são registrados em arquivos separados por farmácia. A trava global evita sobreposição entre execução manual e agendada. `Status-Collector.ps1` mostra o resultado mais recente e o código de saída da tarefa: 0 concluída, 2 acesso negado, 3 parcial, 4 parada segura, 5 limite da origem, 6 falha de API/HMAC, 7 erro, 8 origem desativada, 9 lock ocupado, 10 slot perdido e 11 slot duplicado. O limite inválido, ausente ou acima de 50 impede a execução agendada.

## Segredo e rotação

Use segredo aleatório exclusivo com pelo menos 32 bytes; não reutilize JWT ou credenciais de usuários. Para rotação, suspenda as tarefas, atualize o HMAC na API por canal seguro e então rode `Set-CollectorSecret.ps1` com o novo valor antes de reabilitar as tarefas. Faça isso dentro de uma janela curta; segredo inválido deixa o lote preservado no outbox e interrompe a sincronização.
