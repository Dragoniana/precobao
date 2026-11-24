# Ciclos de recuperação para HTTP 403

O controle é independente para Araújo e Pacheco e continua desligado por
padrão. A coleta só ocorre quando o scheduler, o modo da origem e a confirmação
de autorização também permitem acesso. Não habilite ciclos para contornar uma
recusa explícita, CDN ou WAF. `ARAUJO_403_MANUAL_PAUSE=true` ou
`PACHECO_403_MANUAL_PAUSE=true` pausa a recuperação sem apagar o horário
persistido; desligar `*_403_RECOVERY_ENABLED` também suspende novas tentativas.

## Sequência

Cada ciclo tem exatamente três tentativas. Após 403 nas tentativas 1 e 2, o
checkpoint agenda a próxima tentativa para pelo menos 300 segundos depois,
respeitando um `Retry-After` maior. Após o terceiro 403, o contador de ciclos
falhos aumenta e a próxima tentativa fica agendada para pelo menos uma hora
depois, também respeitando `Retry-After`. Ao vencer o cooldown, começa um novo
ciclo automaticamente enquanto scheduler e política permanecerem habilitados.

O intervalo pode ser aumentado com
`<ORIGEM>_403_RETRY_INTERVAL_SECONDS` (mínimo 300) e o cooldown com
`<ORIGEM>_403_CYCLE_COOLDOWN_SECONDS` (mínimo 3600). Defina
`<ORIGEM>_403_MAX_CYCLES` para limitar quantos ciclos inteiros podem falhar;
`0` significa continuar até uma pausa manual ou mudança da política de acesso.
Ao atingir um limite finito, o circuito abre e exige reset operacional. A partir
do segundo ciclo falho é emitido o evento de alerta `persistent_403_alert`.

O checkpoint persistido contém tentativas do ciclo, ciclos falhos, último 403,
próxima data permitida, cooldown, estado do circuito e último checkpoint válido
da coleta. Reiniciar o container durante a espera não antecipa a próxima
tentativa. A espera usa pausas assíncronas curtas e pode ser interrompida por
cancelamento/SIGTERM/Ctrl-C; o horário permanece no checkpoint.

Um 403 não avança página/offset, não marca ofertas indisponíveis e não altera
preços, imagens ou histórico. O coletor recomeça a descoberta da forma já
definida para cada origem; a persistência existente permanece idempotente. Um
sucesso válido limpa o estado do ciclo e restaura a cadência normal.

## Retomada operacional

Depois de confirmar que a origem liberou o acesso, reabra um circuito limitado
ou pausado explicitamente, na pasta `backend`:

```powershell
poetry run python -m app.scrapers.reset_403_circuit araujo --confirm
poetry run python -m app.scrapers.reset_403_circuit pacheco --confirm
```

Use somente a farmácia revisada. O reset conserva os outros dados do checkpoint
e torna a execução elegível. A validação TLS continua independente por fonte;
`TLS_VERIFY=false` não é correção para 403 e não altera TLS da API, PostgreSQL,
autenticação ou envio HMAC.

## HTTP 206 da Pacheco

O status 206 é aceito somente para respostas JSON com página consistente. Se a
origem enviar `resources` ou `Content-Range`, o início, o tamanho e o total da
faixa devem corresponder ao offset e à quantidade recebida. Sem total explícito,
uma página curta 206 não é tratada como fim do catálogo. Quando a faceta anuncia
o total, o coletor continua paginando até reconciliar esse total ou registra a
lacuna como incompleta.

HTTP 429 respeita `Retry-After` e limita a consulta atual a três tentativas.
Erros transitórios de rede e HTTP 500/502/503/504 mantêm o backoff limitado já
existente. Falhas TLS, parser e banco continuam separadas e não são convertidas
em indisponibilidade de produtos.
