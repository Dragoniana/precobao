"""
Scraper especializado para extração de dados da Drogaria Araujo via HTML parsing.
"""

import asyncio
import contextlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from bs4 import BeautifulSoup

from app.scrapers.base import BasePharmacyScraper
from app.scrapers.partner_feed import load_authorized_partner_feed
from app.services.imagens import image_delivery_rejection_reason


class AraujoScraper(BasePharmacyScraper):
    def __init__(self, num_workers: int = 3, batch_size: int = 50):
        super().__init__(
            farmacia_cnpj="17256512000116",
            farmacia_razao_social="Drogaria Araujo S.A",
            farmacia_nome_fantasia="Araujo",
            farmacia_endereco="Extração via HTML Scraper",
            num_workers=num_workers,
            batch_size=batch_size,
        )
        self.feed_prefix = "ARAUJO_PARTNER_FEED"
        self.state_dir = Path(os.getenv("ARAUJO_STATE_DIR", "data/araujo"))
        self.products_queued = 0

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
        """
        Importa apenas um feed da Araujo fornecido por canal autorizado.

        A coleta direta do site público não é iniciada: os termos oficiais vedam
        mineração de dados, e a VM recebe HTTP 403 em requisição convencional.
        """
        products = await load_authorized_partner_feed(
            prefix=self.feed_prefix, sku_prefix="ARJ"
        )
        if products is None:
            raise RuntimeError(
                "A coleta Araújo exige um feed autorizado; configure "
                "ARAUJO_PARTNER_FEED_FILE ou ARAUJO_PARTNER_FEED_URL."
            )
        for product in products:
            await self.fila.put(product)
        self.products_queued = len(products)
        print(f"Araújo partner feed validated: {len(products)} products queued.")

    def adaptar_parser_araujo_html(self, html_content: str) -> List[Dict[str, Any]]:
        """
        Analisa o HTML da Araujo com BeautifulSoup para extrair produtos.
        Aproveita os metadados JSON do Google Tag Manager embutidos no HTML e
        extrai o EAN através de Regex na URL da imagem.
        """
        catalogo_limpo = []
        soup = BeautifulSoup(markup=html_content, features="html.parser")
        # DEBUG: Liste as classes de todas as divs para ver se o nome mudou
        divs = soup.find_all("div")
        classes_encontradas = set()
        for div in divs:
            if div.get("class"):
                classes_encontradas.update(div.get("class"))

        print(
            "DEBUG: Classes CSS encontradas na página: "
            f"{list(classes_encontradas)[:20]}..."
        )
        # Verifica se o seletor antigo ainda existe
        tem_product_tile = soup.find_all(class_="productTile")
        print(
            "DEBUG: Quantidade de elementos 'productTile' encontrados: "
            f"{len(tem_product_tile)}"
        )

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

                link = f"https://www.araujo.com.br{link_path}" if link_path else ""

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

                if link:
                    catalogo_limpo.append(
                        {
                            "id": id_interno,
                            "sku_interno": f"ARJ{id_interno}",
                            "ean": ean,
                            "name_search": nome,
                            "preco": float(preco),
                            "link": link,
                            "imagem_url": imagem_url,
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
