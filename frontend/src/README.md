# Frontend src — Referência Interna

Documentação detalhada do código-fonte do app Preço Bão. Este README serve como referência para desenvolvedores que trabalham nos hooks, componentes, serviços e tipos do frontend.

## Estrutura

```text
src/
├── app/              # Telas e layouts (Expo Router)
│   ├── _layout.tsx   # Layout raiz com provedores de contexto
│   ├── index.tsx     # Tela inicial (redireciona para home ou login)
│   ├── login.tsx     # Login com email/senha
│   ├── signup.tsx    # Cadastro de nova conta
│   └── (app)/        # Grupo de telas autenticadas/opcionais
│       ├── _layout.tsx          # Layout com drawer e bottom nav
│       ├── home.tsx             # Home: busca, mais baratos, vistos recentemente
│       ├── search.tsx           # Resultados de busca paginados
│       ├── filters.tsx          # Filtros combinados (laboratório, categoria, preço)
│       ├── favorites.tsx        # Favoritos (medicamentos e farmácias)
│       ├── profile.tsx          # Perfil do usuário
│       ├── settings.tsx         # Configurações (tema, notificações)
│       ├── included-pharmacies.tsx  # Farmácias incluídas na plataforma
│       ├── about.tsx            # Página "Saiba mais"
│       ├── medicine/[id].tsx    # Detalhe do medicamento com ofertas
│       └── history/[id].tsx     # Histórico de preços com gráfico
├── components/       # Componentes reutilizáveis
├── constants/        # Constantes de tema e configuração
├── context/          # AppContext: composição dos contextos
├── hooks/            # Hooks personalizados
├── services/         # Chamadas à API e lógica de catálogo
├── types/            # Tipos TypeScript compartilhados
├── theme.ts          # Definição de tema claro/escuro
└── __tests__/        # Testes unitários e de componentes
```

## Hooks

### `useAuthSession.ts`

Gerencia sessão JWT, login, logout, recuperação de senha e edição de perfil.

- **Estado**: `user`, `token`, `isLoading`, `isAuthenticated`
- **Métodos**: `login(email, password)`, `register(data)`, `logout()`, `updateProfile(data)`, `refreshToken()`
- **Armazenamento**: token persistido em `SecureStore` (mobile) ou `localStorage` (web) via `tokenStorage` de `services/api.ts`
- **Callback 401**: quando a API retorna 401 e o token atual é o mesmo da requisição, chama `unauthorizedCallback` para limpar a sessão automaticamente

### `useCatalogState.ts`

Catálogo paginado com busca, filtros combinados e cache limitado.

- **Estado**: `items`, `total`, `page`, `isLoading`, `filters`, `searchQuery`
- **Métodos**: `fetchPage(page)`, `setFilters(filters)`, `setSearch(query)`, `resetFilters()`
- **Lógica de filtros**: filtros dentro da mesma categoria são aditivos (OR); categorias diferentes e demais critérios são combinados (AND)
- **Cache**: mantém as últimas N páginas em memória para navegação instantânea entre resultados já carregados

### `useFavoritesState.ts`

Favoritos persistidos por usuário com sincronização via API.

- **Estado**: `favoriteMedicines`, `favoritePharmacies`, `isLoading`
- **Métodos**: `toggleMedicine(id)`, `togglePharmacy(id)`, `refresh()`
- **Sincronização**: quando há sessão ativa, alterações são enviadas à API imediatamente. Sem sessão, favoritos são mantidos apenas localmente
- **Dois recursos independentes**: medicamentos e farmácias têm endpoints separados (`/users/me/favoritos/medicamentos` e `/users/me/favoritos/farmacias`)

### `use-color-scheme.ts` / `use-color-scheme.web.ts`

Detecta preferência de tema do sistema. A versão `.web.ts` usa `window.matchMedia`; a versão nativa usa `useColorScheme` do React Native.

### `use-theme.ts`

Retorna o tema ativo (claro ou escuro) combinando a preferência do sistema com a escolha manual do usuário nas configurações.

## Componentes

| Componente | Arquivo | Descrição |
|---|---|---|
| `AppDrawerContent` | `AppDrawerContent.tsx` | Conteúdo do menu lateral com links de navegação |
| `BottomNav` | `BottomNav.tsx` | Barra de navegação inferior (home, busca, favoritos, perfil) |
| `FavoriteCard` | `FavoriteCard.tsx` | Cartão compacto de item favoritado |
| `FilterChip` | `FilterChip.tsx` | Chip selecionável para filtros rápidos |
| `GlobalButtonHover` | `GlobalButtonHover.tsx` | Wrapper que adiciona feedback visual de hover em botões |
| `HistoryChart` | `HistoryChart.tsx` | Gráfico de série temporal de preços (usa biblioteca de gráficos) |
| `MedicineCard` | `MedicineCard.tsx` | Cartão de resumo de medicamento com imagem, nome, laboratório e menor preço |
| `MedicineImage` | `MedicineImage.tsx` | Renderiza imagem de medicamento com fallback para placeholder quando URL é inválida ou não disponível |
| `OfferRow` | `OfferRow.tsx` | Linha de oferta com nome da farmácia, preço e disponibilidade |
| `Screen` | `Screen.tsx` | Wrapper padrão para telas com padding, scroll e safe area |
| `SearchBar` | `SearchBar.tsx` | Campo de busca com debounce e botão de limpar |

## Serviços

### `api.ts`

Configuração do cliente HTTP axios.

- **Base URL**: `EXPO_PUBLIC_API_URL` com fallback para `http://127.0.0.1:8000`
- **Interceptor de request**: injeta token JWT no header `Authorization` se disponível
- **Interceptor de response**: detecta 401 e chama `unauthorizedCallback` para limpar sessão automaticamente (apenas se o token da requisição for o mesmo da sessão ativa)
- **Armazenamento de token**: `tokenStorage` abstrai `SecureStore` (mobile) vs `localStorage` (web) para evitar crashes em plataformas sem SecureStore

### `catalog.ts`

Encapsula chamadas de catálogo, busca e filtros.

- `fetchMedicamentos(params)` → GET `/catalogo`
- `fetchMedicamento(id)` → GET `/catalogo/medicamentos/:id`
- `fetchFarmacias()` → GET `/catalogo/farmacias`
- `fetchMaisBaratos(limit)` → GET `/catalogo/mais-baratos`
- `fetchFiltroOpcoes()` → GET `/catalogo/filtros/opcoes`
- `fetchHistory(id, periodo, farmaciaId)` → GET `/api/v1/medicines/:id/history`

## Contexto

### `AppContext.tsx`

Compõe os contextos de sessão, catálogo e favoritos em um único provedor. Envolve todas as telas no `_layout.tsx` raiz.

```tsx
<AppProvider>
  <AuthProvider>
    <CatalogProvider>
      <FavoritesProvider>
        {children}
      </FavoritesProvider>
    </CatalogProvider>
  </AuthProvider>
</AppProvider>
```

## Tipos (`types/api.ts`)

Tipos TypeScript compartilhados que espelham os schemas Pydantic do backend:

- `Medicamento`, `MedicamentoComOfertas`
- `Oferta`, `Farmacia`
- `HistoricoPreco`, `PricePoint`
- `Usuario`, `UsuarioCreate`, `UsuarioUpdate`
- `CatalogoFiltros`, `CatalogoPage`
- `MedicineHistoryResponse`, `PharmacyHistoryStatistics`

## Tema (`theme.ts`)

Define tokens de cor, tipografia e espaçamento para os modos claro e escuro. Os componentes consomem o tema via `useTheme()` hook.

## Testes (`__tests__/`)

Suíte Jest com dados mockados que isolam os testes do backend.

| Arquivo | Cobertura |
|---|---|
| `catalog.test.ts` | Fluxo de catálogo, paginação, filtros |
| `favorites.test.ts` | Toggle de favoritos, sincronização |
| `filters-screen.test.tsx` | Renderização e interação na tela de filtros |
| `history-chart.test.tsx` | Renderização do gráfico de histórico |
| `laboratory-filters.test.ts` | Filtros por laboratório |
| `medicine-card.test.tsx` | Renderização do cartão de medicamento |
| `medicine-image.test.tsx` | Fallback de imagem, URLs válidas/inválidas |
| `session.test.ts` | Login, logout, persistência de token |
| `settings.test.tsx` | Alteração de tema e preferências |

```sh
npm test              # Roda todos os testes
npm test -- --watch   # Modo watch durante desenvolvimento
```