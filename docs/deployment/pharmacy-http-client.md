# Cliente HTTP das fontes Araújo e Pacheco

Os schedulers de Araújo e Pacheco usam HTTPX com validação TLS habilitada por padrão. A seleção é independente por fonte e afeta somente as requisições do scraper correspondente.

Para homologar o cliente histórico, configure no ambiente do container do scraper:

```env
ARAUJO_HTTP_CLIENT=curl_cffi
ARAUJO_CURL_IMPERSONATE=chrome110
ARAUJO_TLS_VERIFY=false

PACHECO_HTTP_CLIENT=curl_cffi
PACHECO_CURL_IMPERSONATE=chrome110
PACHECO_TLS_VERIFY=false
```

O cliente `curl_cffi` está fixado pelo lock de dependências em `0.15.0`. O timeout é configurável por fonte com `ARAUJO_HTTP_TIMEOUT_SECONDS` e `PACHECO_HTTP_TIMEOUT_SECONDS` (padrão de 30 segundos). Os delays entre páginas continuam em `ARAUJO_REQUEST_DELAY_SECONDS` e `PACHECO_REQUEST_DELAY_SECONDS`. Redirecionamentos automáticos ficam desativados.

`TLS_VERIFY=false` deve ser usado somente para uma validação aprovada e limitada. Não se aplica à API, banco PostgreSQL, Caddy, Indiana ou outro serviço. A fonte recebe apenas cabeçalhos públicos da requisição; tokens e credenciais dos feeds não são encaminhados.

O primeiro HTTP 403 encerra a coleta dessa fonte e grava o checkpoint como bloqueado. Não há repetição para 403, rotação de proxy, resolução de CAPTCHA ou troca de perfil. HTTP 429 encerra a execução e o scheduler agenda a próxima tentativa após o `Retry-After` registrado; respostas 5xx usam o backoff limitado existente. Os modos `FEED` e `DISABLED` não mudam.

Ativar `DIRECT` e os schedulers em produção requer homologação independente do acesso real e da integridade dos resultados.
