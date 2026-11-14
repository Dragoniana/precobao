import asyncio
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List

from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal
from app.core.utils import validar_ean13
from app.models.catalogo import CatalogoBase
from app.models.farmacia import Farmacia
from app.models.oferta_farmacia import OfertaFarmacia
from app.schemas.oferta import ProdutoExtraidoSchema
from app.services.enriquecimento import ServicoEnriquecimentoFarmacologico
from app.services.image_registry import upsert_image_references
from app.services.imagens import is_generic_image_url
from app.services.preco_observacoes import registrar_observacao_preco


class BasePharmacyScraper(ABC):
    def __init__(
        self,
        farmacia_cnpj: str,
        farmacia_razao_social: str,
        farmacia_nome_fantasia: str,
        farmacia_endereco: str,
        num_workers: int = 3,
        batch_size: int = 50,
    ):
        self.fila = asyncio.Queue()
        self.num_workers = num_workers
        self.batch_size = batch_size
        self.farmacia_cnpj = farmacia_cnpj
        self.farmacia_razao_social = farmacia_razao_social
        self.farmacia_nome_fantasia = farmacia_nome_fantasia
        self.farmacia_endereco = farmacia_endereco
        self.persistence_stats = {
            "catalog_items_created": 0,
            "offers_created": 0,
            "offers_updated": 0,
            "first_price_observations": 0,
            "price_changes": 0,
            "images_registered": 0,
            "products_without_image": 0,
            "image_errors": 0,
            "persistence_errors": 0,
            "unassociated_products": 0,
        }
        self._persistence_stats_lock = threading.Lock()

    def _record_persistence_stats(self, values: dict[str, int]) -> None:
        with self._persistence_stats_lock:
            for key, value in values.items():
                self.persistence_stats[key] += value

    @abstractmethod
    async def extrair_catalogo(self) -> None:
        """
        Método obrigatório que as classes filhas devem implementar.
        Deve extrair os dados e usar `await self.fila.put(produto_dict)`.
        """
        pass

    async def _worker(self):
        batch = []
        while True:
            produto_dict = await self.fila.get()

            try:
                # Usado para sinalizar fim da extração
                if produto_dict is None:
                    # Salva o restante antes de finalizar o worker
                    if batch:
                        await asyncio.to_thread(self._processar_e_salvar_lote, batch)
                    break

                try:
                    # 1. Validação Pydantic
                    prod_validado = ProdutoExtraidoSchema.model_validate(produto_dict)
                except ValidationError as e:
                    print(f"[AVISO] Ignorando produto inválido: {e}")
                    continue

                # Validação de EAN - limpa EAN se for inválido
                ean_valido = (
                    prod_validado.ean
                    if prod_validado.ean and validar_ean13(prod_validado.ean)
                    else None
                )
                # Usa a cópia validada nas próximas etapas do processamento.
                prod_validado.ean = ean_valido

                # O enriquecimento será preenchido antes da gravação do lote.
                item_enriquecido = {"validado": prod_validado, "enriquecido": None}

                batch.append(item_enriquecido)

                if len(batch) >= self.batch_size:
                    # Salva em thread separada sem bloquear o loop de eventos.
                    await asyncio.to_thread(self._processar_e_salvar_lote, batch.copy())
                    batch.clear()

            except Exception as e:
                print(f"[ERRO WORKER] Falha no processamento: {e}")
            finally:
                self.fila.task_done()

    def _processar_e_salvar_lote(self, batch: List[Dict[str, Any]]) -> None:
        """
        Realiza o Bulk Lookup de enriquecimento e persiste o lote no banco de dados.
        Roda dentro de uma thread separada via asyncio.to_thread.
        """
        if not batch:
            return

        metrics = {key: 0 for key in self.persistence_stats}
        db = SessionLocal()
        try:
            # 1. Enriquecimento em lote (Bulk Lookup)
            batch = ServicoEnriquecimentoFarmacologico.enriquecer_lote(db, batch)

            # 1. Garante que a farmácia existe
            farmacia = (
                db.query(Farmacia).filter(Farmacia.cnpj == self.farmacia_cnpj).first()
            )
            if not farmacia:
                farmacia = Farmacia(
                    cnpj=self.farmacia_cnpj,
                    razao_social=self.farmacia_razao_social,
                    nome_fantasia=self.farmacia_nome_fantasia,
                    endereco_completo=self.farmacia_endereco,
                )
                db.add(farmacia)
                db.commit()
                db.refresh(farmacia)

            # Desduplicação do CatalogoBase no lote atual
            catalogo_values_dict = {}
            for item in batch:
                prod: ProdutoExtraidoSchema = item["validado"]
                enriquecido: dict = item["enriquecido"]

                # Only EAN has a global catalog uniqueness guarantee. Products
                # without EAN are resolved below by exact offer URL or the
                # pharmacy's official SKU, never by display name alone.
                if not prod.ean:
                    continue
                chave_dedup = prod.ean

                catalogo_values_dict[chave_dedup] = {
                    "codigo_barras": prod.ean,
                    "name_search": prod.name_search,
                    "principio_ativo": enriquecido["principio_ativo"],
                    "laboratorio": enriquecido["laboratorio"],
                    "exige_receita": enriquecido["exige_receita"],
                    "categorias": enriquecido["categorias"],
                    "categoria_regulatoria": enriquecido.get("categoria_regulatoria"),
                    "referencia_anvisa": enriquecido.get("referencia_anvisa"),
                    "exige_receita_confirmada": enriquecido.get(
                        "exige_receita_confirmada"
                    ),
                    "controle_especial": enriquecido.get("controle_especial"),
                    "classificacao_fonte": enriquecido.get("classificacao_fonte"),
                }

            catalogo_values = list(catalogo_values_dict.values())

            if catalogo_values:
                existing_eans = {
                    ean
                    for (ean,) in db.query(CatalogoBase.codigo_barras)
                    .filter(
                        CatalogoBase.codigo_barras.in_(
                            [value["codigo_barras"] for value in catalogo_values]
                        )
                    )
                    .all()
                }
                metrics["catalog_items_created"] += sum(
                    value["codigo_barras"] not in existing_eans
                    for value in catalogo_values
                )
                # Upsert CatalogoBase
                stmt = insert(CatalogoBase).values(catalogo_values)
                # O conflito pode ser no codigo_barras
                stmt = stmt.on_conflict_do_update(
                    index_elements=["codigo_barras"],
                    set_={
                        "name_search": stmt.excluded.name_search,
                        "principio_ativo": stmt.excluded.principio_ativo,
                        "laboratorio": stmt.excluded.laboratorio,
                        "exige_receita": stmt.excluded.exige_receita,
                        "categorias": stmt.excluded.categorias,
                        "categoria_regulatoria": func.coalesce(
                            stmt.excluded.categoria_regulatoria,
                            CatalogoBase.categoria_regulatoria,
                        ),
                        "referencia_anvisa": func.coalesce(
                            stmt.excluded.referencia_anvisa,
                            CatalogoBase.referencia_anvisa,
                        ),
                        "exige_receita_confirmada": func.coalesce(
                            stmt.excluded.exige_receita_confirmada,
                            CatalogoBase.exige_receita_confirmada,
                        ),
                        "controle_especial": func.coalesce(
                            stmt.excluded.controle_especial,
                            CatalogoBase.controle_especial,
                        ),
                        "classificacao_fonte": func.coalesce(
                            stmt.excluded.classificacao_fonte,
                            CatalogoBase.classificacao_fonte,
                        ),
                    },
                    where=(CatalogoBase.codigo_barras.is_not(None)),
                )

                db.execute(stmt)
                db.commit()

            # Recupera IDs
            eans_no_lote = [
                item["validado"].ean for item in batch if item["validado"].ean
            ]
            mapa_catalogo = {}
            if eans_no_lote:
                catalogos_com_ean = (
                    db.query(CatalogoBase)
                    .filter(CatalogoBase.codigo_barras.in_(eans_no_lote))
                    .all()
                )
                for c in catalogos_com_ean:
                    mapa_catalogo[c.codigo_barras] = c.id

            # Fetch existing offers once so the shared history policy can compare
            # each new price with the persisted value before mutating it.
            urls_no_lote = [item["validado"].link for item in batch]
            ofertas_existentes_por_url = {}
            if urls_no_lote:
                ofertas_existentes = (
                    db.query(OfertaFarmacia)
                    .filter(OfertaFarmacia.url_origem.in_(urls_no_lote))
                    .with_for_update()
                    .all()
                )
                for oferta in ofertas_existentes:
                    ofertas_existentes_por_url[oferta.url_origem] = oferta

            skus_no_lote_sem_ean = {
                item["validado"].sku_interno
                for item in batch
                if not item["validado"].ean and item["validado"].sku_interno
            }
            ofertas_existentes_por_sku: dict[str, list[OfertaFarmacia]] = {}
            if skus_no_lote_sem_ean:
                for oferta in (
                    db.query(OfertaFarmacia)
                    .filter(
                        OfertaFarmacia.farmacia_id == farmacia.id,
                        OfertaFarmacia.sku_interno.in_(skus_no_lote_sem_ean),
                    )
                    .all()
                ):
                    ofertas_existentes_por_sku.setdefault(
                        oferta.sku_interno, []
                    ).append(oferta)

            # Desduplicação da OfertaFarmacia no lote atual
            oferta_dicts_map = {}
            for item in batch:
                prod: ProdutoExtraidoSchema = item["validado"]
                catalogo_id = None
                if prod.ean and prod.ean in mapa_catalogo:
                    catalogo_id = mapa_catalogo[prod.ean]
                elif not prod.ean:
                    existing_offer = ofertas_existentes_por_url.get(prod.link)
                    if existing_offer is None and prod.sku_interno:
                        sku_matches = ofertas_existentes_por_sku.get(
                            prod.sku_interno, []
                        )
                        if len(sku_matches) == 1:
                            existing_offer = sku_matches[0]
                    if existing_offer is not None:
                        catalogo_id = existing_offer.catalogo_id
                    else:
                        batch_key = prod.sku_interno or prod.link
                        catalogo_id = mapa_catalogo.get(batch_key)
                        if catalogo_id is None:
                            enriched: dict = item["enriquecido"]
                            catalogo_item = CatalogoBase(
                                codigo_barras=None,
                                name_search=prod.name_search,
                                principio_ativo=enriched["principio_ativo"],
                                laboratorio=enriched["laboratorio"],
                                exige_receita=enriched["exige_receita"],
                                categorias=enriched["categorias"],
                                categoria_regulatoria=enriched.get(
                                    "categoria_regulatoria"
                                ),
                                referencia_anvisa=enriched.get("referencia_anvisa"),
                                exige_receita_confirmada=enriched.get(
                                    "exige_receita_confirmada"
                                ),
                                controle_especial=enriched.get("controle_especial"),
                                classificacao_fonte=enriched.get("classificacao_fonte"),
                            )
                            db.add(catalogo_item)
                            db.flush()
                            metrics["catalog_items_created"] += 1
                            catalogo_id = catalogo_item.id
                            mapa_catalogo[batch_key] = catalogo_id

                if catalogo_id:
                    # Chave de desduplicação pela URL origem que é a unique key
                    oferta_dicts_map[prod.link] = {
                        "sku_interno": prod.sku_interno,
                        "preco": prod.preco,
                        "quantidade_estoque": prod.quantidade_estoque,
                        "disponivel": prod.disponivel,
                        "url_origem": prod.link,
                        "fonte_observado_em": prod.fonte_observado_em,
                        "farmacia_id": farmacia.id,
                        "catalogo_id": catalogo_id,
                    }
                else:
                    metrics["unassociated_products"] += 1

            observed_at = datetime.now(timezone.utc)
            changes: list[tuple[OfertaFarmacia, Decimal | None, dict]] = []
            for url, oferta_data in oferta_dicts_map.items():
                offer = ofertas_existentes_por_url.get(url)
                previous_price = offer.preco if offer is not None else None
                if offer is None:
                    offer = OfertaFarmacia(**oferta_data)
                    db.add(offer)
                    metrics["offers_created"] += 1
                else:
                    metrics["offers_updated"] += 1
                    offer.sku_interno = oferta_data["sku_interno"]
                    offer.quantidade_estoque = oferta_data["quantidade_estoque"]
                    offer.disponivel = oferta_data["disponivel"]
                    offer.url_origem = oferta_data["url_origem"]
                    offer.fonte_observado_em = oferta_data["fonte_observado_em"]
                changes.append((offer, previous_price, oferta_data))

            # IDs for new offers are needed to retain the exact offer lineage.
            db.flush()
            for offer, previous_price, oferta_data in changes:
                history_event = registrar_observacao_preco(
                    offer,
                    preco=oferta_data["preco"],
                    observado_em=observed_at,
                    origem=self.farmacia_nome_fantasia,
                    farmacia_id=oferta_data["farmacia_id"],
                    medicamento_id=oferta_data["catalogo_id"],
                    preco_anterior=previous_price,
                )
                if history_event:
                    db.add(history_event)
                    if previous_price is None:
                        metrics["first_price_observations"] += 1
                    else:
                        metrics["price_changes"] += 1

            db.commit()

            # Image references stay outside PostgreSQL. Registry failures do not
            # undo an already committed price or offer.
            image_references = []
            for item in batch:
                prod: ProdutoExtraidoSchema = item["validado"]
                data = oferta_dicts_map.get(prod.link)
                if data is None:
                    continue
                offer = ofertas_existentes_por_url.get(prod.link)
                if offer is None:
                    offer = next(
                        (o for o, _, d in changes if d["url_origem"] == prod.link), None
                    )
                image_references.append(
                    {
                        "image_url": prod.imagem_url,
                        "pharmacy_id": farmacia.id,
                        "pharmacy": self.farmacia_nome_fantasia,
                        "sku": prod.sku_interno,
                        "ean": prod.ean,
                        "product_id": str(prod.id),
                        "product_url": prod.link,
                        "catalog_id": data["catalogo_id"],
                        "offer_id": offer.id if offer else None,
                        "identity_confirmed": bool(offer and prod.sku_interno),
                        "allow_generic": is_generic_image_url(prod.imagem_url or ""),
                    }
                )
            try:
                saved_references = upsert_image_references(image_references)
                metrics["images_registered"] += sum(saved_references)
                metrics["products_without_image"] += len(saved_references) - sum(
                    saved_references
                )
            except Exception as image_error:
                metrics["image_errors"] += len(image_references)
                print(f"[IMAGE REGISTRY] Batch save failed: {image_error}")

        except SQLAlchemyError as e:
            db.rollback()
            metrics["persistence_errors"] += 1
            print(f"[ERRO DB] Falha na persistência em lote: {e}")
        finally:
            db.close()
            self._record_persistence_stats(metrics)

    async def executar_com_backoff(
        self, funcao, *args, max_retries=5, initial_delay=2.0, **kwargs
    ):
        """
        Implementa backoff exponencial para lidar com limites de requisição.
        """
        retries = 0
        delay = initial_delay

        while retries <= max_retries:
            try:
                # Executa a função assíncrona
                response = await funcao(*args, **kwargs)

                # Avalia o status no objeto de resposta se existir
                status = getattr(response, "status_code", 200)
                if status in {403, 429}:
                    print(
                        f"HTTP {status} recebido; encerrando a consulta "
                        "sem retry automático."
                    )
                    return response
                if status >= 500:
                    retries += 1
                    if retries > max_retries:
                        print(f"Max retries excedido para o status HTTP {status}.")
                        return response
                    print(
                        f"Rate limit / Server Error ({status}). Retrying in {delay}s..."
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue

                return response

            except Exception as e:
                # Avalia atributos encadeados de exceção (para libraries como httpx)
                status_obj = getattr(e, "response", None)
                if status_obj is not None:
                    status_code = getattr(status_obj, "status_code", 0)
                    if status_code in {403, 429}:
                        print(
                            f"HTTP {status_code} recebido; encerrando a consulta "
                            "sem retry automático."
                        )
                        raise
                    if status_code >= 500:
                        retries += 1
                        if retries > max_retries:
                            print(
                                f"Max retries excedido para a falha HTTP {status_code}."
                            )
                            raise e
                        print(
                            f"Rate limit / Server Error ({status_code}). "
                            f"Retrying in {delay}s..."
                        )
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue

                # Outras falhas de conexão/timeout
                retries += 1
                if retries > max_retries:
                    print(f"Max retries excedido para erro de rede: {e}")
                    raise e
                print(f"Network/Library error ({e}). Retrying in {delay}s...")
                await asyncio.sleep(delay)
                delay *= 2

    async def run(self):
        """
        Ponto de entrada principal do scraper.
        Inicia os workers consumidores e aguarda a extração.
        """
        # Inicia os workers
        workers = [asyncio.create_task(self._worker()) for _ in range(self.num_workers)]

        try:
            # Roda a extração
            await self.extrair_catalogo()
        finally:
            # Envia sinal de término para os workers (None)
            for _ in range(self.num_workers):
                await self.fila.put(None)

            # Aguarda o processamento de tudo que está na fila
            await self.fila.join()

            # Aguarda as tasks finalizarem
            await asyncio.gather(*workers)
