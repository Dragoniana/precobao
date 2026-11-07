# Frontend — Preço Bão

Aplicativo React Native com Expo Router para Android, iOS e Web. Consome a API FastAPI do backend e oferece busca, filtros, detalhes de medicamentos, histórico de preços, favoritos e autenticação opcional.

## Arquitetura

```text
frontend/
├── src/                  # Código-fonte do app (veja src/README.md)
│   ├── app/              # Telas e layouts (Expo Router)
│   ├── components/       # Componentes reutilizáveis
│   ├── constants/        # Constantes de tema e configuração
│   ├── context/          # AppContext: composição dos contextos
│   ├── hooks/            # Hooks personalizados
│   ├── services/         # Chamadas à API e lógica de catálogo
│   ├── types/            # Tipos TypeScript compartilhados
│   ├── theme.ts          # Definição de tema claro/escuro
│   └── __tests__/        # Testes unitários e de componentes
├── app.json              # Configuração do Expo
├── package.json          # Dependências e scripts npm
└── tsconfig.json         # Configuração TypeScript
```

### Camadas

- **`app/`**: rotas declarativas do Expo Router. `_layout.tsx` envolve todas as telas com os provedores de contexto. O grupo `(app)/` contém as telas acessíveis após login ou como convidado.
- **`components/`**: UI reutilizável. `MedicineCard` exibe resumo de medicamento. `MedicineImage` renderiza imagens com fallback. `HistoryChart` plota série temporal de preços. `SearchBar`, `FilterChip`, `OfferRow`, `BottomNav`, `AppDrawerContent` compõem a navegação e interação.
- **`hooks/`**: estado e lógica desacoplados das telas.
  - `useAuthSession.ts`: sessão JWT, login, logout, recuperação de senha e edição de perfil.
  - `useCatalogState.ts`: catálogo paginado, busca, filtros combinados e cache limitado.
  - `useFavoritesState.ts`: favoritos persistidos por usuário com sincronização via API.
- **`services/`**: comunicação com o backend. `api.ts` configura o cliente HTTP axios com interceptor de token e callback de 401. `catalog.ts` encapsula chamadas de catálogo, busca e filtros.
- **`context/AppContext.tsx`**: compõe os contextos de sessão, catálogo e favoritos em um único provedor.

## Variáveis de ambiente

O frontend **não possui arquivo `.env.example`**. A URL da API é configurada pela variável `EXPO_PUBLIC_API_URL` com fallback hardcoded para `http://127.0.0.1:8000` em `src/services/api.ts`. Para sobrescrever, crie um arquivo `.env` na raiz do diretório `frontend/`:

```sh
EXPO_PUBLIC_API_URL=http://localhost:8000
```

No Android físico ou emulador, `localhost` aponta para o próprio dispositivo. Use o IP da máquina host ou o túnel Cloudflare disponível no `docker-compose.yml` do backend.

## Comandos

```sh
cd frontend
npm install              # Instala dependências
npm start                # Abre o menu do Expo Dev Server (escolha web/android/ios)
npm run web              # Inicia diretamente na web (http://localhost:8081)
npm run android          # Inicia no Android
npm run ios              # Inicia no iOS (macOS apenas)
```

Na raiz do monorepo: `make setup-front` instala dependências, `make run-front` inicia o Expo (`npm start`).

### Verificações

```sh
npx tsc --noEmit                    # Verifica tipos sem emitir arquivos
npm run lint                        # ESLint via Expo
npm test                            # Jest (com --runInBand)
npx expo export --platform web      # Build de produção web
npx expo export --platform android  # Build de produção Android
```

## Fluxo de dados

1. **Autenticação**: login opcional via `/auth/jwt/login`. Token JWT armazenado em `SecureStore` (mobile) ou `localStorage` (web). Entrada como convidado pula a autenticação. Cadastro disponível em `/auth/register`.
2. **Catálogo**: consultado em páginas no servidor via `useCatalogState`. Filtros dentro da mesma categoria são aditivos (OR); categorias diferentes e demais critérios são combinados (AND).
3. **Detalhes**: carregados sob demanda por identificador quando o usuário abre a tela de detalhe.
4. **Favoritos**: dois recursos independentes (medicamentos e farmácias), sincronizados com a API quando há sessão ativa. Persistidos localmente para exibição imediata.
5. **Histórico**: série temporal de preços carregada por medicamento na tela de histórico, com filtro por período e farmácia.
6. **Imagens**: renderizadas por `MedicineImage` com fallback para placeholder quando a URL é inválida ou não disponível.

## Endpoints consumidos

Os paths abaixo refletem os endpoints reais do backend. A base é definida por `EXPO_PUBLIC_API_URL`.

### Catálogo

| Método | Endpoint | Uso |
|---|---|---|
| GET | `/catalogo` | Lista paginada com busca e filtros |
| GET | `/catalogo/farmacias` | Lista farmácias cadastradas |
| GET | `/catalogo/mais-baratos` | Medicamentos mais baratos |
| GET | `/catalogo/filtros/opcoes` | Opções disponíveis para filtros dinâmicos |
| GET | `/catalogo/medicamentos/:id` | Detalhes do medicamento com ofertas |

### Autenticação e Usuários

| Método | Endpoint | Uso |
|---|---|---|
| POST | `/auth/jwt/login` | Login com email e senha, retorna JWT |
| POST | `/auth/register` | Cadastro de nova conta |
| GET | `/users/me` | Perfil do usuário autenticado |
| PATCH | `/users/me` | Atualiza perfil |

### Favoritos

| Método | Endpoint | Uso |
|---|---|---|
| GET | `/users/me/favoritos/medicamentos` | Lista medicamentos favoritos |
| POST | `/users/me/favoritos/medicamentos/:id` | Adiciona medicamento favorito |
| DELETE | `/users/me/favoritos/medicamentos/:id` | Remove medicamento favorito |
| GET | `/users/me/favoritos/farmacias` | Lista farmácias favoritas |
| POST | `/users/me/favoritos/farmacias/:id` | Adiciona farmácia favorita |
| DELETE | `/users/me/favoritos/farmacias/:id` | Remove farmácia favorita |

### Histórico

| Método | Endpoint | Uso |
|---|---|---|
| GET | `/api/v1/medicines/:id/history` | Histórico de preços (parâmetros: `periodo`, `farmacia_id`) |

## Recursos não implementados

- Alertas de preço
- Preferência de idioma
- Notificações push

## Testes

A suíte cobre: fluxo de catálogo, filtros de laboratório, sessões, favoritos, gráfico de histórico, cartão de medicamento, imagem de medicamento e tela de filtros. Dados mockados isolam os testes do backend.

```sh
npm test                 # Roda todos os testes
npm test -- --watch      # Modo watch durante desenvolvimento
```