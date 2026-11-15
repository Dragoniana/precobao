// Interfaces baseadas nos Schemas do FastAPI

export interface Oferta {
  id: number;
  farmacia_id: number;
  catalogo_id: number;
  preco: number | string;
  quantidade_estoque: number;
  disponivel: boolean;
  url_origem: string;
  imagem_url?: string;
  contexto_chave?: string;
  canal_venda?: string | null;
  contexto_local?: string | null;
  cep?: string | null;
  cidade?: string | null;
  uf?: string | null;
  loja_id?: string | null;
  loja_nome?: string | null;
  vendedor?: string | null;
  confiabilidade?: string | null;
  fonte_observado_em?: string | null;
  imagem_rejeitada?: boolean;
  imagem_generica?: boolean;
  criado_em?: string;
  atualizado_em?: string | null;
  observado_em?: string | null;

  farmacia_nome?: string;
  farmacia?: {
    nome_fantasia?: string;
    razao_social?: string;
  } | null;
}

export interface Medicamento {
  id: number;
  codigo_barras: string;
  nome: string;
  name_search?: string;
  principio_ativo: string;
  laboratorio: string;
  exige_receita: boolean;
  categorias: string[];
  categoria_regulatoria?: string | null;
  referencia_anvisa?: boolean | null;
  exige_receita_confirmada?: boolean | null;
  controle_especial?: boolean | null;
  classificacao_fonte?: string | null;
  imagem_url?: string | null;
  imagem_urls?: string[];
  imagem_ref?: string | null;
  imagem_armazenada?: boolean;
  imagem_fonte?: string | null;
  imagem_status?: string | null;
  imagem_motivo_rejeicao?: string | null;
  ofertas: Oferta[];
}

export interface HistoricoPrecoPonto {
  data_registro: string;
  preco: number;
  contexto_chave?: string;
  canal_venda?: string | null;
  contexto_local?: string | null;
  cep?: string | null;
  cidade?: string | null;
  uf?: string | null;
  loja_id?: string | null;
  loja_nome?: string | null;
  fonte_observado_em?: string | null;
}

export interface HistoricoPrecoSerie {
  farmacia_id: number;
  farmacia_nome?: string;
  contexto_chave?: string;
  serie_id?: string;
  historico: HistoricoPrecoPonto[];
}
