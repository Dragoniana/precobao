# Recuperação de senha por email

O fluxo público usa `POST /auth/password-reset/request` e
`POST /auth/password-reset/confirm`. O primeiro sempre retorna a mesma mensagem
genérica para endereços cadastrados e não cadastrados. O segundo exige um token
aleatório, de uso único, válido por 20 minutos, e senhas iguais.

O hash SHA-256 do token é armazenado na tabela preexistente
`password_recovery_codes`; o token em texto claro só permanece na memória da
requisição e é entregue ao SMTP em uma tarefa de fundo. Cada nova solicitação
invalida tokens anteriores. A tabela e os registros antigos não são apagados.
As rotas antigas de emissão e consumo de códigos manuais foram removidas e a
tela antiga redireciona para `/forgot-password`.

## Configuração de produção

Configurar no arquivo de ambiente protegido do serviço `api`:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_STARTTLS=true
SMTP_USERNAME=precobao01@gmail.com
SMTP_PASSWORD=<senha de aplicativo, inserida por mecanismo seguro>
SMTP_FROM_EMAIL=precobao01@gmail.com
SMTP_FROM_NAME="Preço Bão"
SMTP_TIMEOUT_SECONDS=10
PASSWORD_RESET_URL=https://precobao.app/reset-password
RATE_LIMIT_PASSWORD_RESET_REQUESTS_PER_HOUR=3
RATE_LIMIT_PASSWORD_RESET_CONFIRMS_PER_HOUR=5
```

Não guardar a senha em código, arquivos versionados, linha de comando ou logs.
O cliente SMTP valida certificados TLS usando o trust store do sistema e exige
STARTTLS. Falhas de envio são registradas somente por tipo de erro, sem email,
token, conteúdo da mensagem ou resposta SMTP. A tela `/reset-password` é servida
com `Cache-Control: no-store` e `Referrer-Policy: no-referrer`; após ler o token,
o frontend remove a query da barra de endereço e não usa analytics.

O email é multipart (texto simples e HTML) e informa a validade de 20 minutos.
O envio ocorre depois da resposta HTTP em tarefa de fundo síncrona executada no
thread pool do Starlette, com timeout configurável.

## Sessões existentes

A senha é atualizada pelo hash seguro do `UserManager`. Os JWT já emitidos não
possuem revogação individual no sistema atual e podem permanecer válidos até sua
expiração normal (no máximo uma hora). Um novo login utiliza apenas a senha nova.

## Validação e implantação

Testes usam SMTP falso local; não enviam mensagens externas. Antes do deploy,
configurar `SMTP_PASSWORD` diretamente no mecanismo seguro da VM, confirmar a
conectividade SMTP com TLS e enviar uma única mensagem para uma conta de teste
controlada. Não aplicar migration: o esquema atual já armazena o hash e a
expiração dos tokens de recuperação.
