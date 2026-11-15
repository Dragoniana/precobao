# Coletor Windows — Araújo e Pacheco

Este pacote executa os coletores existentes no Windows e envia somente os lotes validados à API HTTPS. Não configura acesso ao PostgreSQL. Por padrão, a API e as fontes estão desativadas e nenhum agendamento é criado.

## Instalação e configuração

1. Clone/atualize o repositório no computador autorizado e abra PowerShell no repositório.
2. Execute `Set-ExecutionPolicy -Scope Process Bypass` e depois `.\deploy\windows-scraper-collector\Install-Collector.ps1`.
3. Configure a API já implantada em `%LOCALAPPDATA%\PrecoBaoCollector\settings.json`: `api_ready=true`, `collector_enabled=true`, URL HTTPS da rota e fontes em `DIRECT` com `access_confirmed=true`. Mantenha `timezone=America/Sao_Paulo` e os horários `08:00,20:00`.
4. Guarde o mesmo segredo HMAC exclusivo do coletor/API com `.\deploy\windows-scraper-collector\Set-CollectorSecret.ps1`. O arquivo fica cifrado via DPAPI para o usuário atual e ACL da pasta restrita; o segredo não entra no Git nem nos logs.
5. Antes de cada atualização do repositório, pare o agendamento, atualize a branch e reinstale dependências com `Install-Collector.ps1`.

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

# Pedir parada segura (termina a página/lote atual) e suspender tarefas
.\deploy\windows-scraper-collector\Stop-Collector.ps1

# Instalar/remover agendamento após prontidão e aprovação
.\deploy\windows-scraper-collector\Register-Schedule.ps1 -Enable
.\deploy\windows-scraper-collector\Unregister-Schedule.ps1
```

## Execução e recuperação

O Agendador de Tarefas executa às 08h e 20h do fuso local validado como `America/Sao_Paulo`. `StartWhenAvailable` permite uma única recuperação após retorno do computador; execuções atrasadas até 120 minutos são anotadas e coalescidas numa só execução. Atrasos maiores são anotados como perdidos e ignorados. IDs de slot persistidos impedem disparo duplicado; a trava global impede sobreposição. Os checkpoints e outbox atômicos estão sob `%LOCALAPPDATA%\PrecoBaoCollector\state`. Se a API falhar após coleta, o outbox permanece para reenvio no próximo ciclo.

`Stop-Collector.ps1` cria um marcador local. A coleta em andamento termina a sincronização da página atual, para antes de buscar a próxima e mantém qualquer outbox pendente; tarefas agendadas ficam suspensas. Uma execução manual/agendada posterior remove o marcador ao iniciar. Logs não registram corpo, HMAC ou credenciais.

## Segredo e rotação

Use segredo aleatório exclusivo com pelo menos 32 bytes; não reutilize JWT ou credenciais de usuários. Para rotação, suspenda as tarefas, atualize o HMAC na API por canal seguro e então rode `Set-CollectorSecret.ps1` com o novo valor antes de reabilitar as tarefas. Faça isso dentro de uma janela curta; segredo inválido deixa o lote preservado no outbox e interrompe a sincronização.
