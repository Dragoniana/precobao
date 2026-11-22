"""
Scraper especializado para extração de dados da Drogaria Araujo via HTML parsing.
"""

import asyncio
import contextlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from app.scrapers.base import BasePharmacyScraper
from app.scrapers.partner_feed import load_authorized_partner_feed
from app.scrapers.source_http_client import open_source_http_client, source_http_config
from app.scrapers.source_modes import (
    SourceHTTPError,
    SourceMode,
    direct_access_ready,
    validate_source_response,
)
from app.scrapers.source_modes import (
    source_mode as configured_source_mode,
)
from app.services.imagens import image_delivery_rejection_reason


class AraujoScraper(BasePharmacyScraper):
    CATALOG_URL = "https://www.araujo.com.br/medicamentos"
    PAGE_SIZE = 48

    def __init__(
        self,
        num_workers: int = 3,
        batch_size: int = 50,
        *,
        source_mode: SourceMode | str | None = None,
        request_delay: float | None = None,
        max_pages: int | None = None,
        allow_page_limit: bool = False,
        stop_requested_callback: Callable[[], bool] | None = None,
    ):
        super().__init__(
            farmacia_cnpj="17256512000116",
            farmacia_razao_social="Drogaria Araujo S.A",
            farmacia_nome_fantasia="Araujo",
            farmacia_endereco="Extração via HTML Scraper",
            num_workers=num_workers,
            batch_size=batch_size,
        )
        self.source_mode = (
            SourceMode(source_mode.upper())
            if isinstance(source_mode, str)
            else source_mode or configured_source_mode("araujo")
        )
        self.feed_prefix = "ARAUJO_PARTNER_FEED"
        self.state_dir = Path(os.getenv("ARAUJO_STATE_DIR", "data/araujo"))
        self.source_checkpoint_path = self.state_dir / "source-checkpoint.json"
        self.request_delay = float(
            os.getenv("ARAUJO_REQUEST_DELAY_SECONDS", "1")
            if request_delay is None
            else request_delay
        )
        self.max_pages = int(
            os.getenv("ARAUJO_MAX_PAGES", "200") if max_pages is None else max_pages
        )
        self.allow_page_limit = allow_page_limit
        self.stop_requested_callback = stop_requested_callback
        self.http_timeout = source_http_config("araujo").timeout
        if self.request_delay < 0 or self.max_pages < 1:
            raise ValueError("Invalid Araújo request delay or page limit")
        self.products_queued = 0
        self.products_callback: (
            Callable[[list[dict[str, Any]]], Awaitable[None]] | None
        ) = None

    @contextlib.contextmanager
    def _run_lock(self):
        """Prevent manual and scheduled imports from running concurrently."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with (self.state_dir / "scraper.lock").open("a+b") as lock_file:
            try:
                if os.name == "nt":
                    import msvcrt

                    lock_file.seek(0)
                    if not lock_file.read(1):
                        lock_file.write(b"0")
                        lock_file.flush()
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise RuntimeError(
                    "Outra coleta Araújo já está em execução."
                ) from error
            try:
                yield
            finally:
                if os.name == "nt":
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    async def run(self) -> None:
        """Run the shared importer under a host/container-wide file lock."""
        with self._run_lock():
            await super().run()

    async def extrair_catalogo(self) -> None:
        """Use exactly one explicitly selected source path; never fall back."""
        if self.source_mode is SourceMode.DISABLED:
            return
        if self.source_mode is SourceMode.FEED:
            products = await load_authorized_partner_feed(
                prefix=self.feed_prefix, sku_prefix="ARJ"
            )
            if products is None:
                raise RuntimeError("ARAUJO_PARTNER_FEED_NOT_CONFIGURED")
            for product in products:
                await self.fila.put(product)
            self.products_queued = len(products)
            print(f"Araújo partner feed validated: {len(products)} products queued.")
            return
        if not direct_access_ready("araujo"):
            raise RuntimeError("ARAUJO_DIRECT_ACCESS_NOT_CONFIRMED")
        await self._extract_direct_catalog()

    def _write_direct_checkpoint(self, state: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=".araujo-source-", suffix=".tmp", dir=self.state_dir
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.source_checkpoint_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    async def _extract_direct_catalog(self) -> None:
        """Page the current public catalog with the configured source client."""
        state: dict[str, Any] = {
            "source": "araujo",
            "mode": SourceMode.DIRECT.value,
            "status": "RUNNING",
            "started_at": datetime.now(UTC).isoformat(),
            "resume_strategy": "durable_page_and_item_cursor",
            "last_completed_page": 0,
            "next_page": 1,
            "next_item_index": 0,
            "products_queued": 0,
        }
        try:
            previous = json.loads(
                self.source_checkpoint_path.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            previous = {}
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("ARAUJO_SOURCE_CHECKPOINT_INVALID") from error
        if isinstance(previous, dict):
            previous_status = str(previous.get("status", "")).upper()
            if previous_status != "COMPLETE":
                legacy_page = previous.get("last_completed_page", 0)
                fallback_page = int(legacy_page) if legacy_page else 1
                state["next_page"] = max(
                    1, int(previous.get("next_page", fallback_page))
                )
                state["next_item_index"] = max(
                    0, int(previous.get("next_item_index", 0))
                )
        if state["next_page"] > self.max_pages:
            state["next_page"] = 1
            state["next_item_index"] = 0
        start_page = state["next_page"]
        start_item_index = state["next_item_index"]
        self._write_direct_checkpoint(state)
        headers = {
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
            "User-Agent": "PrecoBao authorized catalog client/1.0",
        }
        try:
            async with open_source_http_client("araujo", headers=headers) as client:
                for page in range(start_page, self.max_pages + 1):
                    response = await self.executar_com_backoff(
                        client.get,
                        self.CATALOG_URL,
                        params={"page": page},
                        timeout=self.http_timeout,
                    )
                    validate_source_response(response, accepted={200})
                    source_cards = self._catalog_card_count(response.text)
                    if source_cards == 0:
                        if page == 1:
                            raise RuntimeError("ARAUJO_CATALOG_SELECTOR_MISMATCH")
                        break
                    products = self.adaptar_parser_araujo_html(response.text)
                    if not products:
                        raise RuntimeError("ARAUJO_CATALOG_PRODUCTS_UNPARSEABLE")
                    page_item_start = start_item_index if page == start_page else 0
                    if page_item_start >= len(products):
                        raise RuntimeError("ARAUJO_CHECKPOINT_ITEM_CURSOR_INVALID")
                    products = products[page_item_start:]
                    if not products:
                        raise RuntimeError("ARAUJO_CATALOG_PRODUCTS_UNPARSEABLE")
                    if self.products_callback is not None:
                        delivered = await self.products_callback(products)
                        delivered = (
                            len(products) if delivered is None else int(delivered)
                        )
                    else:
                        for product in products:
                            await self.fila.put(product)
                        delivered = len(products)
                    if delivered < 0 or delivered > len(products):
                        raise RuntimeError("ARAUJO_INVALID_DELIVERED_PRODUCT_COUNT")
                    self.products_queued += delivered
                    next_page = page + 1
                    next_item_index = 0
                    if delivered < len(products):
                        next_page = page
                        next_item_index = page_item_start + delivered
                    state.update(
                        last_completed_page=page,
                        next_page=next_page,
                        next_item_index=next_item_index,
                        products_queued=self.products_queued,
                        updated_at=datetime.now(UTC).isoformat(),
                    )
                    self._write_direct_checkpoint(state)
                    if delivered < len(products):
                        state.update(
                            status="PARTIAL",
                            stop_reason="configured_product_limit_within_page",
                            finished_at=datetime.now(UTC).isoformat(),
                        )
                        self._write_direct_checkpoint(state)
                        return
                    if self.stop_requested_callback and self.stop_requested_callback():
                        state.update(
                            status="STOPPED",
                            stop_reason="safe_stop_requested",
                            finished_at=datetime.now(UTC).isoformat(),
                        )
                        self._write_direct_checkpoint(state)
                        return
                    if source_cards < self.PAGE_SIZE:
                        state.update(next_page=1, next_item_index=0)
                        break
                    if self.request_delay:
                        await asyncio.sleep(self.request_delay)
                else:
                    if not self.allow_page_limit:
                        raise RuntimeError("ARAUJO_MAX_PAGES_REACHED")
                    state.update(
                        status="PARTIAL",
                        stop_reason="configured_page_limit",
                        next_page=1,
                        next_item_index=0,
                        finished_at=datetime.now(UTC).isoformat(),
                    )
                    self._write_direct_checkpoint(state)
                    return
            state.update(
                status="COMPLETE",
                finished_at=datetime.now(UTC).isoformat(),
                next_page=1,
                next_item_index=0,
            )
            self._write_direct_checkpoint(state)
            print(
                f"Araújo direct catalog completed: "
                f"{self.products_queued} products queued."
            )
        except SourceHTTPError as error:
            state.update(
                status=(
                    "ACCESS_BLOCKED"
                    if error.status_code == 403
                    else "RATE_LIMITED"
                    if error.status_code == 429
                    else "ERROR"
                ),
                stop_reason=f"http_{error.status_code}",
                retry_after_seconds=error.retry_after_seconds,
                finished_at=datetime.now(UTC).isoformat(),
            )
            self._write_direct_checkpoint(state)
            raise
        except Exception as error:
            state.update(
                status="ERROR",
                stop_reason=type(error).__name__,
                finished_at=datetime.now(UTC).isoformat(),
            )
            self._write_direct_checkpoint(state)
            raise

    @staticmethod
    def _catalog_card_count(html_content: str) -> int:
        soup = BeautifulSoup(markup=html_content, features="html.parser")
        return len(soup.find_all(name="div", class_="productTile"))

    def adaptar_parser_araujo_html(self, html_content: str) -> List[Dict[str, Any]]:
        """
        Analisa o HTML da Araujo com BeautifulSoup para extrair produtos.
        Aproveita os metadados JSON do Google Tag Manager embutidos no HTML e
        extrai o EAN através de Regex na URL da imagem.
        """
        catalogo_limpo = []
        soup = BeautifulSoup(markup=html_content, features="html.parser")
        cards_produtos = soup.find_all(name="div", class_="productTile")

        for card in cards_produtos:
            try:
                # 1. Link do produto
                link_path = card.get(key="data-url")
                if isinstance(link_path, list):
                    link_path = str(link_path[0])
                elif link_path:
                    link_path = str(link_path)
                else:
                    link_path = ""

                link = urljoin("https://www.araujo.com.br/", link_path)
                parsed_link = urlsplit(link)
                if (
                    parsed_link.scheme != "https"
                    or parsed_link.hostname != "www.araujo.com.br"
                ):
                    continue

                # 2. Dados GTM
                gtm_tag = card.find("div", class_="gtmContainer__productTile")
                gtm_data_str = gtm_tag.get("data-gtmga4data") if gtm_tag else "{}"

                if isinstance(gtm_data_str, list):
                    gtm_data_str = str(gtm_data_str[0])
                elif gtm_data_str:
                    gtm_data_str = str(gtm_data_str)

                gtm_data = json.loads(gtm_data_str) if gtm_data_str else {}

                # 3. Nome e Preço
                nome = gtm_data.get("item_name", "")
                if not nome:
                    tag_nome = card.find("a", class_="productTile__name")
                    nome = tag_nome.text.strip() if tag_nome else "Nome não encontrado"

                preco = gtm_data.get("price", 0.0)
                if not preco:
                    tag_preco = card.find("span", class_="productPrice__price")
                    if tag_preco:
                        texto_preco = (
                            tag_preco.text.replace("R$", "")
                            .replace(".", "")
                            .replace(",", ".")
                            .strip()
                        )
                        preco = float(texto_preco)

                # 4. Imagem e EAN
                tag_img = card.find(name="img", class_="productTile__imageWrapper__img")
                imagem_url = (
                    tag_img.get(key="data-src") or tag_img.get(key="src")
                    if tag_img
                    else ""
                )

                if isinstance(imagem_url, list):
                    imagem_url = str(imagem_url[0])
                elif imagem_url:
                    imagem_url = str(imagem_url)
                else:
                    imagem_url = ""

                if imagem_url and imagem_url.startswith("/"):
                    imagem_url = f"https://www.araujo.com.br{imagem_url}"
                elif imagem_url and imagem_url.startswith("//"):
                    imagem_url = f"https:{imagem_url}"

                if imagem_url and image_delivery_rejection_reason(imagem_url):
                    imagem_url = ""

                # 5. ID interno: imagem e EAN só podem ser atribuídos se o
                # caminho da imagem estiver associado ao SKU deste card.
                id_interno = card.get(key="data-pid", default="")
                if isinstance(id_interno, list):
                    id_interno = str(id_interno[0])
                elif id_interno:
                    id_interno = str(id_interno)

                image_parts = [part for part in imagem_url.split("/") if part]
                if imagem_url and str(id_interno) not in image_parts[:-1]:
                    image_product_id = image_parts[-2] if len(image_parts) > 1 else ""
                    if image_product_id.isdigit():
                        imagem_url = ""

                ean = ""
                if imagem_url:
                    match = re.search(pattern=r"(\d{13,14})", string=imagem_url)
                    if match:
                        ean = match.group(1).strip().replace("-", "")

                if link and id_interno.isdigit() and nome and float(preco) > 0:
                    unavailable_markers = (
                        "indisponível",
                        "indisponivel",
                        "esgotado",
                        "sem estoque",
                    )
                    card_text = card.get_text(" ", strip=True).casefold()
                    is_available = not any(
                        marker in card_text for marker in unavailable_markers
                    ) and not card.select_one(
                        ".productTile__unavailable, [data-available='false']"
                    )
                    catalogo_limpo.append(
                        {
                            "id": id_interno,
                            "sku_interno": f"ARJ{id_interno}",
                            "ean": ean,
                            "name_search": str(nome)[:150],
                            "preco": float(preco),
                            "disponivel": is_available,
                            "quantidade_estoque": 1 if is_available else 0,
                            "link": link,
                            "imagem_url": imagem_url,
                            "fonte_observado_em": datetime.now(UTC),
                        }
                    )
            except Exception as e:
                print(f"Erro ao parsear um card específico: {e}")
                continue

        return catalogo_limpo


if __name__ == "__main__":
    print("Iniciando varredura na Drogaria Araujo via HTML com Nova Arquitetura...")
    scraper = AraujoScraper(num_workers=3, batch_size=50)
    try:
        asyncio.run(scraper.run())
    except KeyboardInterrupt:
        print("\nInterrompido pela usuária.")
