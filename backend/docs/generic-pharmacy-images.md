# Fallbacks genéricos de imagens de farmácias

Imagens de medicamentos publicadas oficialmente pela Araújo ou Pacheco são aceitas quando a URL usa HTTPS, o host pertence à farmácia correspondente e os bytes passam pela validação de formato, tamanho e integridade. A classificação genérica usa os nomes e URLs conhecidos pelo serviço; marcar uma imagem arbitrária como genérica no pedido não contorna a validação.

O fallback é associado somente à oferta e à farmácia, com `catalog_id` nulo, `is_generic=true` e sem papel de imagem principal. Assim, EAN divergente pode permanecer registrado na oferta sem reescrever o EAN ou associar a ilustração a outro medicamento. Uma imagem específica permanece vinculada ao catálogo e tem prioridade na interface.

A API entrega o fallback no endpoint da oferta, com ETag. O catálogo não inclui bytes nem URLs genéricas como imagens específicas. A interface tenta primeiro as imagens específicas e só então as imagens genéricas das ofertas.

Na interface, o fallback deve corresponder à oferta de referência do cartão ou da linha. Cartões gerais usam a oferta disponível de menor preço; linhas de comparação usam a oferta daquela linha; detalhes usam a menor oferta disponível. Se a oferta de referência não possuir fallback próprio, a interface mantém o placeholder neutro em vez de usar a imagem de outra farmácia. Uma imagem genérica vinculada a uma oferta não é, por si só, um ativo universal reutilizável da farmácia.

O frontend também fornece um cartão ilustrativo próprio do Preço Bão para as origens reconhecidas Araújo, Indiana e Pacheco quando a imagem específica e o fallback armazenado da oferta não carregam. O cartão usa somente texto com o nome da farmácia e a indicação “Imagem ilustrativa”; não é um ativo oficial, não imita a marca e não representa uma embalagem. Quando a origem não puder ser identificada, o placeholder neutro permanece. Não promover uma embalagem de medicamento específico a fallback universal.

O coletor salva uma confirmação local de imagens genéricas já aceitas para não baixá-las novamente na mesma instalação. O cache fica no diretório de estado do coletor, não contém segredos e só é atualizado depois que a API confirma o upload. A outbox remove o manifesto e os bytes somente após essa confirmação. Para recuperar apenas pendências existentes, use `Sync-Pending-Images.ps1`; o comando não acessa os sites das farmácias nem sincroniza preços.

A migration `c4a8f32d1e9b` torna `catalog_id` anulável, adiciona a classificação genérica e mantém no máximo um vínculo específico e um fallback genérico por oferta. O downgrade recusa prosseguir enquanto existirem fallbacks genéricos, evitando associá-los automaticamente a medicamentos.
