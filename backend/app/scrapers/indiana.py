"""
app.scrapers.indiana
====================

Este módulo fornece utilitários para a extração (scraping) do catálogo de
medicamentos da Farmácia Indiana consumindo diretamente sua API REST.

A utilidade principal deste módulo é automatizar a varredura de produtos da loja,
lidando com a paginação da API, extraindo campos de interesse (como EAN, nome,
preço e imagens) e despachando os dados limpos para a camada de persistência
(banco de dados) de forma assíncrona para não bloquear o loop de eventos.
"""

import asyncio
import logging
import os
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List

import httpx
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal
from app.core.utils import validar_ean13
from app.models.catalogo import CatalogoBase
from app.models.farmacia import Farmacia
from app.models.oferta_farmacia import OfertaFarmacia
from app.schemas.oferta import ProdutoExtraidoSchema
from app.services.enriquecimento import ServicoEnriquecimentoFarmacologico
from app.services.image_registry import upsert_image_reference
from app.services.imagens import select_vtex_item_image
from app.services.preco_observacoes import registrar_observacao_preco

logger = logging.getLogger(__name__)


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(1.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(1.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


async def extrair_todos_produtos(
    *, max_pages: int | None = None, request_delay: float | None = None
) -> dict[str, int | str]:
    """
    Extrai todos os medicamentos iterando sobre a paginação da API REST.

    A função faz requisições assíncronas para a API da Farmácia Indiana,
    controlando o ``offset`` e a página atual. Quando obtém sucesso, repassa
    os dados brutos para a função :func:`adaptar_parser_rest` e despacha os
    dados limpos para o banco de dados através de uma thread separada,
    evitando bloqueios de I/O.

    O loop é interrompido automaticamente caso a API retorne status 404 ou
    uma lista vazia de produtos.

    :raises httpx.HTTPStatusError: Se a API retornar erros da família 4xx
        (exceto o 404 de fim de catálogo tratado) ou 5xx.
    :raises Exception: Para qualquer falha genérica ou de conexão inesperada.

    :returns: None
    :rtype: None
    """
    url_base_api = "https://www.farmaciaindiana.com.br/api/catalog_system/pub/products/search/medicamentos"

    headers = {
        "Accept": "application/json",
        "User-Agent": "PrecoBao/1.0 (+https://precobao.app)",
    }

    itens_por_pagina = 49
    max_pages = max_pages or int(os.getenv("INDIANA_MAX_PAGES", "0")) or None
    request_delay = (
        float(os.getenv("INDIANA_REQUEST_DELAY_SECONDS", "2"))
        if request_delay is None
        else request_delay
    )
    if request_delay < 1:
        raise ValueError("INDIANA_REQUEST_DELAY_SECONDS must be at least 1")
    offset = 0
    pagina = 1
    stats: dict[str, int | str] = {
        "pages": 0,
        "products": 0,
        "errors": 0,
        "retries": 0,
    }
    consecutive_errors = 0

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        while max_pages is None or pagina <= max_pages:
            # Offset 2500 was the last accepted public page. Stop locally
            # before sending the next request at 2550; never probe past it.
            if offset > 2500:
                stats["stop_reason"] = "public_endpoint_offset_limit_2500"
                logger.info("Indiana public endpoint offset limit reached")
                break
            try:
                url_paginada = (
                    f"{url_base_api}?_from={offset}&_to={offset + itens_por_pagina}"
                )
                resp = await client.get(url_paginada)

                # Tratamento de fim de catálogo explícito
                if resp.status_code == 404:
                    logger.info("Indiana catalog ended (404)")
                    break

                # Levanta erro apenas para 4xx e 5xx (200 e 206 passam direto)
                resp.raise_for_status()

                produtos_brutos = resp.json()

                if not produtos_brutos:
                    logger.info("Indiana catalog returned an empty page")
                    break

                produtos_limpos = adaptar_parser_rest(produtos_brutos)

                # Despacha para thread de I/O
                persisted = await asyncio.to_thread(salvar_no_banco, produtos_limpos)
                if not persisted:
                    stats["errors"] += 1
                    logger.error("Indiana persistence failed page=%s", pagina)
                    break

                stats["pages"] += 1
                stats["products"] += len(produtos_limpos)
                consecutive_errors = 0
                logger.info("Indiana page=%s products=%s", pagina, len(produtos_limpos))

                pagina += 1
                offset += itens_por_pagina + 1
                if max_pages is None or pagina <= max_pages:
                    await asyncio.sleep(request_delay + random.uniform(0, 0.5))

            except httpx.HTTPStatusError as e:
                status = e.response.status_code

                # VTEX caps public catalog pagination at offset 2500. Treat
                # this explicit endpoint limit as a partial discovery boundary;
                # do not probe alternate paths or work around it. Existing
                # known offers are refreshed individually by the worker.
                if status == 400 and offset >= 2500:
                    stats["stop_reason"] = "public_endpoint_offset_limit_2500"
                    logger.info("Indiana public endpoint offset limit reached")
                    break

                if status == 429 or status >= 500:
                    # Respect Retry-After, otherwise use capped backoff; never
                    # retry indefinitely or evade a technical block.
                    retry_after = resp.headers.get("Retry-After", "")
                    delay = _retry_after_seconds(retry_after)
                    if delay is None:
                        delay = min(300.0, 2.0 ** min(consecutive_errors + 1, 8))
                    consecutive_errors += 1
                    stats["retries"] += 1
                    if consecutive_errors >= 3 or delay > 3600:
                        stats["errors"] += 1
                        logger.error(
                            "Indiana stopping after HTTP %s page=%s", status, pagina
                        )
                        break
                    logger.warning(
                        "Indiana HTTP %s page=%s; retry in %.1fs", status, pagina, delay
                    )
                    await asyncio.sleep(delay)
                    continue
                stats["errors"] += 1
                logger.error(
                    "Indiana blocked or failed HTTP page=%s status=%s", pagina, status
                )
                break

            except (httpx.TimeoutException, httpx.TransportError) as e:
                consecutive_errors += 1
                stats["retries"] += 1
                logger.warning(
                    "Indiana request error page=%s type=%s", pagina, type(e).__name__
                )
                if consecutive_errors >= 3:
                    stats["errors"] += 1
                    break
                await asyncio.sleep(min(60.0, 2.0**consecutive_errors))
            except Exception:
                stats["errors"] += 1
                logger.exception("Indiana scrape failed page=%s", pagina)
                break
    return stats


def adaptar_parser_rest(produtos_brutos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Analisa e limpa a resposta bruta de produtos em JSON.

    Recebe a lista de dicionários brutos da API VTEX/REST da loja e filtra
    apenas as informações essenciais para o sistema, garantindo também
    fallbacks, como a busca de código EAN em referências secundárias.

    :param produtos_brutos: Lista de dicionários representando o JSON original
        de produtos retornados pela API.
    :type produtos_brutos: List[Dict[str, Any]]

    :returns: Lista de dicionários contendo os dados padronizados de cada
        produto (id, ean, nome, preco, link, imagem_url).
    :rtype: List[Dict[str, Any]]
    """
    catalogo_limpo = []

    for prod in produtos_brutos:
        itens = prod.get("items", [])
        if not itens:
            continue

        item_principal = itens[0]

        # Correção: Fallback do EAN inserido
        ean = item_principal.get("ean")
        if not ean:
            refs = item_principal.get("referenceId", [])
            ean = refs[0].get("Value") if refs else ""

        vendedores = item_principal.get("sellers", [])
        preco = 0.0
        if vendedores:
            preco = vendedores[0].get("commertialOffer", {}).get("Price", 0.0)

        item_id = str(item_principal.get("itemId") or "")
        imagem_url = select_vtex_item_image(
            item_principal,
            expected_ean=str(ean).strip() if ean else None,
            expected_sku=item_id,
            allow_generic=True,
        )

        catalogo_limpo.append(
            {
                "id": item_id,
                "sku_interno": f"IND{item_id or '000'}",
                "ean": ean,
                "name_search": prod.get("productName"),
                "preco": preco,
                "link": prod.get("link"),
                "imagem_url": imagem_url,
            }
        )

    return catalogo_limpo


def salvar_no_banco(produtos: List[Dict[str, Any]]) -> bool:
    """
    Persiste uma lista de produtos no banco de dados utilizando a nova estrutura.
    Atualiza o preço da oferta caso o medicamento já exista no catálogo.
    """
    db = SessionLocal()

    try:
        cnpj_padrao = "00000000000100"
        farmacia = db.query(Farmacia).filter(Farmacia.cnpj == cnpj_padrao).first()

        if not farmacia:
            farmacia = Farmacia(
                cnpj=cnpj_padrao,
                razao_social="Farmácia Indiana - Web Scraper",
                nome_fantasia="Indiana",
                endereco_completo="Extração API REST",
            )
            db.add(farmacia)
            db.commit()
            db.refresh(farmacia)

        observed_at = datetime.now(timezone.utc)
        history_changes = []
        image_changes = []
        for prod in produtos:
            try:
                prod_validado = ProdutoExtraidoSchema.model_validate(prod)
            except ValidationError as e:
                print(f"[AVISO] Ignorando produto inválido: {e}")
                continue

            ean_validado = prod_validado.ean
            sku_interno = prod_validado.sku_interno

            if ean_validado and validar_ean13(ean_validado):
                # Tenta encontrar o produto no Catálogo Base
                catalogo_item = (
                    db.query(CatalogoBase)
                    .filter(CatalogoBase.codigo_barras == ean_validado)
                    .first()
                )
            else:
                catalogo_item = None
                ean_validado = None

            # Resolve a no-EAN listing by its stable official URL/SKU before
            # creating catalog data. A product name is not a unique identity.
            oferta_existente = (
                db.query(OfertaFarmacia)
                .filter(OfertaFarmacia.url_origem == prod_validado.link)
                .first()
            )
            if not oferta_existente and sku_interno:
                sku_matches = (
                    db.query(OfertaFarmacia)
                    .filter(
                        OfertaFarmacia.farmacia_id == farmacia.id,
                        OfertaFarmacia.sku_interno == sku_interno,
                    )
                    .all()
                )
                if len(sku_matches) == 1:
                    oferta_existente = sku_matches[0]
            if oferta_existente:
                produto_associado = db.get(CatalogoBase, oferta_existente.catalogo_id)
                if produto_associado:
                    if catalogo_item and catalogo_item.id != produto_associado.id:
                        logger.warning(
                            "Skipping Indiana SKU with conflicting EAN "
                            "association sku=%s",
                            sku_interno,
                        )
                        continue
                    if (
                        ean_validado
                        and produto_associado.codigo_barras
                        and produto_associado.codigo_barras != ean_validado
                    ):
                        logger.warning(
                            "Skipping Indiana offer whose existing catalog EAN "
                            "differs sku=%s",
                            sku_interno,
                        )
                        continue
                    if ean_validado and not produto_associado.codigo_barras:
                        produto_associado.codigo_barras = ean_validado
                    catalogo_item = produto_associado

            # Se não existir no catálogo, cria um novo registo
            # Se não existir no catálogo, cria um novo registo enriquecido
            if not catalogo_item:
                # Executa o enriquecimento fora da thread principal.
                ean_para_enriquecer = ean_validado if ean_validado else ""
                dados_enriquecidos = (
                    ServicoEnriquecimentoFarmacologico.enriquecer_produto(
                        db, ean_para_enriquecer, prod_validado.name_search
                    )
                )

                catalogo_item = CatalogoBase(
                    codigo_barras=ean_validado,
                    name_search=prod_validado.name_search,
                    principio_ativo=dados_enriquecidos["principio_ativo"],
                    laboratorio=dados_enriquecidos["laboratorio"],
                    exige_receita=dados_enriquecidos["exige_receita"],
                    categorias=dados_enriquecidos["categorias"],
                    categoria_regulatoria=dados_enriquecidos.get(
                        "categoria_regulatoria"
                    ),
                    referencia_anvisa=dados_enriquecidos.get("referencia_anvisa"),
                    exige_receita_confirmada=dados_enriquecidos.get(
                        "exige_receita_confirmada"
                    ),
                    controle_especial=dados_enriquecidos.get("controle_especial"),
                    classificacao_fonte=dados_enriquecidos.get("classificacao_fonte"),
                )
                db.add(catalogo_item)
                db.commit()  # Necessário para gerar o ID que será usado na oferta
                db.refresh(catalogo_item)

            # Se não houver oferta, cria uma nova
            if not oferta_existente:
                nova_oferta = OfertaFarmacia(
                    sku_interno=sku_interno,
                    preco=prod_validado.preco,
                    quantidade_estoque=1,
                    disponivel=True,
                    url_origem=prod_validado.link,
                    farmacia_id=farmacia.id,
                    catalogo_id=catalogo_item.id,
                )
                db.add(nova_oferta)
                oferta_existente = nova_oferta
                previous_price = None
            # Se a oferta já existir, atualiza apenas os dados dinâmicos
            else:
                previous_price = oferta_existente.preco

                # Atualiza somente se a URL mudou.
                if oferta_existente.url_origem != prod_validado.link:
                    oferta_existente.url_origem = prod_validado.link

            oferta_existente.sku_interno = sku_interno
            oferta_existente.disponivel = True
            oferta_existente.quantidade_estoque = 1
            history_changes.append(
                (
                    oferta_existente,
                    previous_price,
                    catalogo_item.id,
                    farmacia.id,
                    prod_validado.preco,
                )
            )
            image_changes.append((prod_validado, oferta_existente, catalogo_item.id))

        db.flush()
        for offer, previous_price, catalogo_id, farmacia_id, price in history_changes:
            history_event = registrar_observacao_preco(
                offer,
                preco=price,
                observado_em=observed_at,
                origem="Indiana",
                farmacia_id=farmacia_id,
                medicamento_id=catalogo_id,
                preco_anterior=previous_price,
            )
            if history_event:
                db.add(history_event)
        db.commit()
        for product, offer, catalog_id in image_changes:
            try:
                upsert_image_reference(
                    image_url=product.imagem_url,
                    pharmacy_id=farmacia.id,
                    pharmacy="Indiana",
                    sku=product.sku_interno,
                    ean=product.ean,
                    product_id=str(product.id),
                    product_url=product.link,
                    catalog_id=catalog_id,
                    offer_id=offer.id,
                )
            except Exception as image_error:
                print(f"[IMAGE REGISTRY] Save failed: {product.link}: {image_error}")
        return True

    except SQLAlchemyError as e:
        db.rollback()
        logger.exception("Indiana database persistence failed: %s", type(e).__name__)
        return False
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    logger.info("Starting Indiana catalog scan")
    try:
        asyncio.run(extrair_todos_produtos())
    except KeyboardInterrupt:
        print("\nInterrompido pela usuária.")
