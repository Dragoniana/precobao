# Fallbacks genéricos de imagens de farmácias

Imagens de medicamentos publicadas oficialmente pela Araújo ou Pacheco são aceitas quando a URL usa HTTPS, o host pertence à farmácia correspondente e os bytes passam pela validação de formato, tamanho e integridade. A classificação genérica usa os nomes e URLs conhecidos pelo serviço; marcar uma imagem arbitrária como genérica no pedido não contorna a validação.

O fallback é associado somente à oferta e à farmácia, com `catalog_id` nulo, `is_generic=true` e sem papel de imagem principal. Assim, EAN divergente pode permanecer registrado na oferta sem reescrever o EAN ou associar a ilustração a outro medicamento. Uma imagem específica permanece vinculada ao catálogo e tem prioridade na interface.

A API entrega o fallback no endpoint da oferta, com ETag. O catálogo não inclui bytes nem URLs genéricas como imagens específicas. A interface tenta primeiro as imagens específicas e só então as imagens genéricas das ofertas.

O coletor salva uma confirmação local de imagens genéricas já aceitas para não baixá-las novamente na mesma instalação. O cache fica no diretório de estado do coletor, não contém segredos e só é atualizado depois que a API confirma o upload. A outbox remove o manifesto e os bytes somente após essa confirmação. Para recuperar apenas pendências existentes, use `Sync-Pending-Images.ps1`; o comando não acessa os sites das farmácias nem sincroniza preços.

A migration `c4a8f32d1e9b` torna `catalog_id` anulável, adiciona a classificação genérica e mantém no máximo um vínculo específico e um fallback genérico por oferta. O downgrade recusa prosseguir enquanto existirem fallbacks genéricos, evitando associá-los automaticamente a medicamentos.
