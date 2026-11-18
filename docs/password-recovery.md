# Recuperação de senha sem email

## Para usuários

1. Enquanto ainda consegue entrar, abra **Perfil → Segurança → Código de recuperação**.
2. Confirme a senha atual. O código é aleatório, válido por até um ano e mostrado uma única vez.
3. Guarde-o em um gerenciador de senhas ou outro local privado. Ele não é enviado por email nem fica disponível para consulta posterior.
4. Se gerar outro código, o anterior deixa de funcionar.
5. Na tela de login, use **Esqueci minha senha?**, informe o email associado, avance e envie o código junto com a nova senha.

O email identifica a conta, mas não comprova sua titularidade. A troca só ocorre com o código válido, não expirado e ainda não usado. Após dez códigos incorretos para um código ativo, ele é invalidado. A API também limita tentativas por endereço de rede.

## Usuários antigos sem código

Quem não gerou um código antes de perder acesso não pode se recuperar apenas informando o email. O endpoint de recuperação não cria nem envia códigos.

Procedimento assistido:

1. O suporte registra uma solicitação sem pedir senha ou código existente.
2. Um operador verifica a identidade usando evidência independente do email da conta, estabelecida antes da solicitação. O email sozinho, dados públicos e conhecimento de dados cadastrais não bastam.
3. Um segundo operador aprova a recuperação. Registre no chamado quem aprovou e o resultado, sem registrar senha, código ou documento de identidade completo.
4. Um superusuário autorizado define uma senha temporária pela operação administrativa existente `PATCH /users/{id}`. O acesso superusuário deve ser protegido e a operação deve ser auditada. A senha temporária é entregue somente pelo canal independente usado para verificar a identidade.
5. O usuário entra, troca a senha e gera seu próprio código em **Perfil → Segurança**.

Se não houver evidência independente ou canal seguro para entregar a senha temporária, não altere a conta. Encaminhe o caso para validação humana adicional. Nunca use o email como única prova de identidade.

## Operação técnica

- `POST /auth/password-recovery/codes` exige sessão válida e confirmação da senha atual. Retorna o código uma vez com `Cache-Control: no-store`.
- `POST /auth/password-recovery/reset` compara somente o hash SHA-256 do código; códigos inválidos, expirados, inexistentes e emails não cadastrados recebem a mesma resposta pública.
- Códigos são de uso único. Nova emissão revoga códigos anteriores. O hash da senha continua sob o PasswordHelper do FastAPI Users.
- Auditoria registra apenas a ação e o resultado; não registra email, identificador de usuário, senha, código nem corpo da requisição.
- Limites padrão: cinco tentativas de recuperação por hora e três emissões por hora por cliente. O limitador atual é em memória e pressupõe uma única instância da API. Antes de escalar horizontalmente, substitua-o por armazenamento compartilhado ou limite na borda.
- A migration `d30c5f9b24a1` é aditiva; downgrade remove somente a tabela de códigos e deve ser usado apenas se a migration ainda não tiver sido usada para recuperar contas.
