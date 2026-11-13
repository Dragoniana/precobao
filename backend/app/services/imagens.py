"""Safe product image selection and HTTP validation for pharmacy catalog data."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.models.catalogo import CatalogoBase

ALLOWED_IMAGE_HOSTS = {
    "farmaciaindiana.vteximg.com.br": "Indiana",
    "drogariaspacheco.vteximg.com.br": "Pacheco",
    "i3-imagens-prd.araujo.com.br": "Araujo",
    "www.araujo.com.br": "Araujo",
    "www.farmaciasnissei.com.br": "Nissei",
    "farmaciamodelo.com.br": "FarmaciaModelo",
}
GENERIC_IMAGE_MARKERS = (
    "logo",
    "banner",
    "placeholder",
    "sem-imagem",
    "no-image",
    "tarjadogenerico",
    "tarjado-generico",
    "medicamento-tarjado",
    "caixa-medicamento",
    "caixa-azul",
    "caixa-vermelha",
    "caixa-neo-quimica",
    "caixa-da-indiana",
    "caixa-merck",
    "caixa-althaia",
    "remedioindiana",
    "image-to-upload",
    "medicamentos-caixa",
    "imagem-indisponivel",
    "medicamento-tarjado-generico",
    "de-referencia-tarja-vermelha",
    "referencia-tarja-vermelha",
    "tarja-vermelha-em-comprimido",
)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
KNOWN_GENERIC_ASSETS = {
    # Semavy asset is a pricing promotion rendered at multiple CDN sizes.
    "239046_7891142985217_1.webp",
    # Browser review: Indiana prescription template, not the Prati package.
    "7899547503845_0.jpg",
    # Shared across different GTINs; browser inspection shows Indiana templates.
    "lonium-40mg-30cpr.jpg",
    "7891317006013_0.jpg",
    "cicloprimogyna-21-comprimidos.jpg",
    "inspra-25mg-30-comprimidos.jpg.jpg",
    # Alternative Araújo assets are generic blue medicine-box illustrations.
    "125419_7908236801213_1.webp",
    "130589_7896637023443_1.webp",
    # Singleton Indiana file also showed a generic box in visual sampling.
    "cefalexina-500mg-uniao-quimica-generico-8-capsulas.jpg",
    "231489_7897705203095_2.webp",
    # Araújo serves this generic package illustration for the Glyxambi listing.
    "128666_7896026306751_1.webp",
    # Indiana's SKU 122816 maps Glyxambi to an ANVISA generic illustration.
    "7896026306751.jpg",
    # Browser review: Araujo Mounjaro assets are combo promotions, not packs.
    "239898_7896382709135_1.webp",
    "239899_7896382709111_1.webp",
    "239360_7896382709173_1.webp",
    "239363_7896382709159_1.webp",
    "239367_7896382709197_1.webp",
    "239366_7896382709210_1.webp",
    # Browser review: Pacheco Mounjaro assets are combo promotions/templates.
    "887455-2.jpg",
    "887528-dpa.png",
    "888060-2.jpg",
    "887951-2.jpg",
    "888079-2.jpg",
    "888087-2.jpg",
    # Exact SKU-linked Araújo assets visually confirmed as generic illustrations.
    "129154_7891721201806_1.webp",
    "128048_7896112486787_1.webp",
    "121300_7896004706795_1.webp",
    "132278_7896523227450_1.webp",
    "122285_7896714226705_1.webp",
    "121651_7896422501682_1.webp",
    "181628_7896714212104_1.webp",
    "181664_7896714231143_1.webp",
    "122169_7899095242203_1.webp",
    "120824_7898569760496_1.webp",
    "122493_7898049796540_1.webp",
    "121740_7896422516921_1.webp",
    "121411_7896004749648_1.webp",
    "121336_7896004711034_1.webp",
    "128765_7896015518875_1.webp",
    "129998_7895858016241_1.webp",
    "127500_7896269900150_1.webp",
    "121381_7896004729640_1.webp",
    "121690_7896422506625_1.webp",
    "120803_7898216367429_1.webp",
    "119931_7896004787725_1.webp",
    "121383_7896004734392_1.webp",
    "125570_7898948648131_1.webp",
    "vacinas-caixa.jpg",
    "121090_7891317021955_1.webp",
    "138222_7896004749624_1.webp",
    "122172_7899095243866_1.webp",
    "121271_7896004704036_1.webp",
    "122502_7898060132600_1.webp",
    "121378_7896004729442_1.webp",
    "121664_7896422505062_1.webp",
    "122213_7899547503845_1.webp",
    "128757_7896015516659_1.webp",
    "136239_7897076922540_1.webp",
    "120136_7896181918691_1.webp",
    "119807_7896004732633_1.webp",
    "119928_7896004784083_1.webp",
    "121290_7896004705989_1.webp",
    "121453_7896004759845_1.webp",
    "7896241202494_0.jpg",
}
_generic_content = json.loads(
    Path(__file__).with_name("generic_image_hashes.json").read_text(encoding="utf-8")
)
KNOWN_GENERIC_CONTENT_HASHES = frozenset(
    digest for hashes in _generic_content["sources"].values() for digest in hashes
)
KNOWN_GENERIC_URLS = frozenset(
    json.loads(
        Path(__file__).with_name("generic_image_urls.json").read_text(encoding="utf-8")
    )["urls"]
)
MAX_IMAGE_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class ImageCheck:
    ok: bool
    source: str | None = None
    status_code: int | None = None
    final_url: str | None = None
    reason: str | None = None
    content_sha256: str | None = None


def source_for_url(url: str) -> str | None:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return None
    return ALLOWED_IMAGE_HOSTS.get(host)


def image_delivery_rejection_reason(url: str) -> str | None:
    """Reject unsafe or technically unsupported URLs, not generic artwork."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return "Invalid image URL."
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return "Image URL must use public HTTPS."
    if port not in (None, 443):
        return "Image URL port is not allowed."
    if source_for_url(url) is None:
        return "Image host is not allowed."
    filename = unquote(parsed.path.rsplit("/", 1)[-1]).lower()
    if not any(filename.endswith(extension) for extension in IMAGE_EXTENSIONS):
        return "Unsupported image format."
    return None


def offer_image_delivery_rejection_reason(
    image_url: str, product_url: str, pharmacy_name: str | None
) -> str | None:
    """Ensure an offer image is hosted by the same pharmacy as its product."""
    reason = image_delivery_rejection_reason(image_url)
    if reason:
        return reason
    source = source_for_url(image_url)
    pharmacy = (pharmacy_name or "").casefold()
    expected = (
        "Araujo"
        if "araujo" in pharmacy
        else "Indiana"
        if "indiana" in pharmacy
        else "Nissei"
        if "nissei" in pharmacy
        else "FarmaciaModelo"
        if "modelo" in pharmacy
        else "Pacheco"
        if "pacheco" in pharmacy
        else None
    )
    if source != expected or expected is None:
        return "Image host does not match the offer pharmacy."
    try:
        product_host = (urlsplit(product_url).hostname or "").lower()
    except ValueError:
        return "Invalid product URL."
    domain = {
        "Araujo": "araujo.com.br",
        "Indiana": "farmaciaindiana.com.br",
        "Nissei": "farmaciasnissei.com.br",
        "FarmaciaModelo": "farmaciamodelo.com.br",
        "Pacheco": "drogariaspacheco.com.br",
    }[expected]
    if product_host != domain and not product_host.endswith(f".{domain}"):
        return "Product URL does not match the offer pharmacy."
    if expected == "Araujo":
        try:
            product_id = re.search(r"/(\d+)\.html?$", urlsplit(product_url).path)
            image_parts = [part for part in urlsplit(image_url).path.split("/") if part]
        except ValueError:
            return "Invalid product or image URL."
        image_product_id = image_parts[-2] if len(image_parts) > 1 else ""
        if (
            product_id
            and image_product_id.isdigit()
            and image_product_id != product_id.group(1)
        ):
            return "Image path product ID does not match the offer."
    return None


def catalog_image_matches_record(catalog: CatalogoBase) -> bool:
    """Confirm a stored catalog image by its recorded source identifiers."""
    image_url = catalog.imagem_url
    if not image_url or image_delivery_rejection_reason(image_url):
        return False
    source = source_for_url(image_url)
    if not source or source != catalog.imagem_fonte:
        return False

    image_eans = embedded_eans(image_url)
    recorded_ean_match = bool(
        catalog.imagem_match == "ean"
        and catalog.codigo_barras
        and gtin_matches(catalog.imagem_ean, catalog.codigo_barras)
    )
    ean_mismatch = bool(
        catalog.codigo_barras
        and image_eans
        and not any(gtin_matches(catalog.codigo_barras, ean) for ean in image_eans)
    )
    generic_art = bool(image_rejection_reason(image_url))
    sku_item_match = bool(
        catalog.imagem_match == "sku_item"
        and re.fullmatch(r"(?:IND|ARJ)\d+", catalog.imagem_sku_origem or "")
    )
    if ean_mismatch and not (generic_art or recorded_ean_match or sku_item_match):
        return False

    if recorded_ean_match:
        return True

    if source == "Pacheco":
        if catalog.imagem_match in {"sku", "sku_item", "ean", "catalog_duplicate"}:
            return True
        return False

    if source == "Araujo":
        sku = re.fullmatch(r"ARJ(\d+)", catalog.imagem_sku_origem or "")
        image_parts = [part for part in urlsplit(image_url).path.split("/") if part]
        return bool(sku and sku.group(1) in image_parts[:-1])

    indiana_sku = re.fullmatch(r"IND\d+", catalog.imagem_sku_origem or "")
    if source != "Indiana" or not indiana_sku:
        return False
    if catalog.imagem_match in {"sku", "catalog_duplicate", "sku_item"}:
        return True
    # Older records can preserve the exact Indiana SKU and source EAN while
    # lacking the catalog EAN. This is safe only for the pharmacy's generic
    # image and is needed for the Indiana "no product photo" artwork.
    return bool(
        catalog.imagem_match == "ean"
        and catalog.imagem_ean
        and not catalog.codigo_barras
        and generic_art
    )


def embedded_eans(url: str) -> set[str]:
    path = urlsplit(url).path.rsplit("/", 1)[-1]
    return set(re.findall(r"(?<!\d)\d{13,14}(?!\d)", path))


def gtin_matches(first: str | None, second: str | None) -> bool:
    """Treat EAN-13 and its GTIN-14 form with one leading zero as equivalent."""
    if not first or not second:
        return False
    if first == second:
        return True
    return (len(first) == 13 and second == f"0{first}") or (
        len(second) == 13 and first == f"0{second}"
    )


def is_generic_image_url(url: str) -> bool:
    """Return whether a URL is known to serve generic or pharmacy artwork."""
    if url in KNOWN_GENERIC_URLS:
        return True
    if source_for_url(url) is None:
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    filename = unquote(parsed.path.rsplit("/", 1)[-1]).lower()
    normalized_filename = re.sub(r"[^a-z0-9]+", "", filename)
    return bool(
        filename == "generico.webp"
        or filename in KNOWN_GENERIC_ASSETS
        or any(
            re.sub(r"[^a-z0-9]+", "", marker) in normalized_filename
            for marker in GENERIC_IMAGE_MARKERS
        )
    )


def image_rejection_reason(url: str) -> str | None:
    """Audit-level rejection, including appearance-only generic assets."""
    if url in KNOWN_GENERIC_URLS:
        return "O conteÃºdo Ã© uma ilustraÃ§Ã£o genÃ©rica da farmÃ¡cia."
    reason = image_delivery_rejection_reason(url)
    if reason:
        return reason
    parsed = urlsplit(url)
    filename = unquote(parsed.path.rsplit("/", 1)[-1]).lower()
    normalized_filename = re.sub(r"[^a-z0-9]+", "", filename)
    if (
        filename == "generico.webp"
        or filename in KNOWN_GENERIC_ASSETS
        or any(
            re.sub(r"[^a-z0-9]+", "", marker) in normalized_filename
            for marker in GENERIC_IMAGE_MARKERS
        )
    ):
        return "A fonte retornou um Ã­cone ou imagem genÃ©rica."
    return None


def validate_image_association(
    *,
    image_url: str,
    pharmacy_name: str,
    sku: str | None,
    ean: str | None,
    product_url: str | None,
    identity_confirmed: bool = False,
) -> tuple[bool, str | None, str | None]:
    """Check source and product identifiers before any network request."""
    reason = image_delivery_rejection_reason(image_url)
    if reason:
        return False, None, reason
    source = source_for_url(image_url)
    normalized_pharmacy = pharmacy_name.casefold()
    expected_source = (
        "Araujo"
        if "araujo" in normalized_pharmacy
        else "Indiana"
        if "indiana" in normalized_pharmacy
        else "Nissei"
        if "nissei" in normalized_pharmacy
        else "FarmaciaModelo"
        if "modelo" in normalized_pharmacy
        else "Pacheco"
        if "pacheco" in normalized_pharmacy
        else None
    )
    if expected_source is None:
        return False, source, "A origem da oferta não possui validador configurado."
    if source != expected_source:
        return False, source, "A imagem não pertence à farmácia da oferta."

    image_eans = embedded_eans(image_url)
    if (
        image_eans
        and ean
        and not any(gtin_matches(ean, image_ean) for image_ean in image_eans)
        and not identity_confirmed
        and not image_rejection_reason(image_url)
    ):
        return False, source, "O EAN do arquivo diverge sem confirmação de SKU do item."

    if source == "Araujo":
        offer_sku = re.fullmatch(r"ARJ(\d+)", sku or "")
        if not offer_sku or not product_url:
            return False, source, "SKU da Araújo ausente para conferir a imagem."
        product_id = re.search(r"/(\d+)\.html?$", urlsplit(product_url).path)
        image_parts = [part for part in urlsplit(image_url).path.split("/") if part]
        if not product_id or product_id.group(1) != offer_sku.group(1):
            return False, source, "O SKU da imagem diverge da página do produto."
        image_product_id = image_parts[-2] if len(image_parts) > 1 else ""
        if image_product_id.isdigit() and image_product_id != offer_sku.group(1):
            return False, source, "O SKU da imagem diverge da página do produto."
        if (
            not image_product_id.isdigit()
            and not identity_confirmed
            and not image_rejection_reason(image_url)
        ):
            return False, source, "A imagem não tem vínculo verificável com o SKU."
        return True, "ean" if image_eans else "sku", None

    if source in {"Nissei", "FarmaciaModelo"}:
        try:
            product_host = (urlsplit(product_url or "").hostname or "").lower()
        except ValueError:
            return False, source, "URL do produto inválida."
        domain = (
            "farmaciasnissei.com.br" if source == "Nissei" else "farmaciamodelo.com.br"
        )
        if (
            not identity_confirmed
            or not ean
            or not product_url
            or (product_host != domain and not product_host.endswith(f".{domain}"))
        ):
            return False, source, "Imagem externa exige vínculo confirmado por EAN."
        return True, "ean", None

    if source == "Pacheco":
        try:
            product_host = (urlsplit(product_url or "").hostname or "").lower()
        except ValueError:
            return False, source, "URL do produto inválida."
        if (
            not identity_confirmed
            or not product_url
            or (
                product_host != "drogariaspacheco.com.br"
                and not product_host.endswith(".drogariaspacheco.com.br")
            )
        ):
            return (
                False,
                source,
                "Imagem da Pacheco exige vínculo confirmado com o domínio oficial.",
            )
        match_kind = (
            "ean"
            if image_eans
            and ean
            and any(gtin_matches(ean, image_ean) for image_ean in image_eans)
            else "sku"
        )
        return True, match_kind, None

    if not re.fullmatch(r"IND\d+", sku or "") or not product_url:
        return False, source, "SKU da Indiana ausente para conferir a imagem."
    if offer_image_delivery_rejection_reason(image_url, product_url, pharmacy_name):
        return False, source, "A imagem não está associada a uma página Indiana válida."
    return (
        True,
        "ean"
        if image_eans and any(gtin_matches(ean, image_ean) for image_ean in image_eans)
        else "sku",
        None,
    )


def register_image_candidate(
    catalog: CatalogoBase,
    *,
    image_url: str | None,
    pharmacy_name: str,
    sku: str | None,
    ean: str | None,
    product_url: str | None,
    identity_confirmed: bool = False,
) -> bool:
    """Keep a scrape image as pending until the batch verifier checks HTTP data."""
    if not image_url:
        return False
    valid, match_kind, reason = validate_image_association(
        image_url=image_url,
        pharmacy_name=pharmacy_name,
        sku=sku,
        ean=ean,
        product_url=product_url,
        identity_confirmed=identity_confirmed,
    )
    if not valid:
        if catalog.imagem_status != "verificada":
            catalog.imagem_motivo_rejeicao = reason
        return False
    if catalog.imagem_status == "verificada":
        return True
    catalog.imagem_url = image_url
    catalog.imagem_fonte = source_for_url(image_url)
    catalog.imagem_sku_origem = sku
    catalog.imagem_ean = ean
    filename_eans = embedded_eans(image_url)
    if ean and any(gtin_matches(ean, image_ean) for image_ean in filename_eans):
        catalog.imagem_match = "ean"
    elif identity_confirmed and ean and filename_eans:
        catalog.imagem_match = "sku_item"
    else:
        catalog.imagem_match = match_kind
    catalog.imagem_status = "pendente"
    catalog.imagem_atualizada_em = datetime.now(UTC)
    catalog.imagem_falhas_consecutivas = 0
    catalog.imagem_ultima_falha = None
    catalog.imagem_motivo_rejeicao = None
    return True


def select_vtex_item_image(
    item: dict,
    *,
    expected_ean: str | None,
    expected_sku: str | None = None,
    allow_generic: bool = False,
) -> str | None:
    """Select a photo attached to an exact VTEX SKU/EAN item.

    VTEX sometimes keeps an older EAN in an image filename after a product image
    is attached to the correct SKU. The item relationship is stronger evidence
    than that filename, but only after the returned item itself matches identity.
    """
    item_ean = str(item.get("ean") or "").strip()
    item_sku = str(item.get("itemId") or "").strip()
    if expected_ean and item_ean != expected_ean:
        return None
    if expected_sku and item_sku != expected_sku:
        return None
    if not expected_ean and not expected_sku:
        return None
    images = item.get("images") or []
    candidates: list[tuple[int, str]] = []
    for image in images:
        url = str(image.get("imageUrl") or "").strip()
        if not url or (
            image_delivery_rejection_reason(url)
            if allow_generic
            else image_rejection_reason(url)
        ):
            continue
        ids = embedded_eans(url)
        ean_priority = (
            0
            if any(gtin_matches(expected_ean, image_ean) for image_ean in ids)
            else 1
            if not ids
            else 2
        )
        candidates.append((ean_priority, url))
    if not candidates:
        return None
    candidates.sort(key=lambda value: value[0])
    return candidates[0][1]


def select_araujo_product_image(
    html: str, *, sku: str, ean: str | None, product_url: str
) -> str | None:
    """Read the product photo, not the generic thumbnail in Araújo listings."""
    soup = BeautifulSoup(html, "html.parser")
    candidates = [
        (tag.get("content"), True)
        for tag in soup.select('meta[property="og:image"], meta[name="twitter:image"]')
    ]
    for tag in soup.select("img[src], img[data-src]"):
        candidates.extend(((tag.get("data-src"), False), (tag.get("src"), False)))
    for candidate, page_associated in candidates:
        if not candidate:
            continue
        url = urljoin(product_url, unescape(str(candidate).strip()))
        valid, _, _ = validate_image_association(
            image_url=url,
            pharmacy_name="Araujo",
            sku=sku,
            ean=ean,
            product_url=product_url,
            identity_confirmed=page_associated,
        )
        if valid:
            return url
    return None


async def verify_image_url(
    url: str,
    *,
    timeout_seconds: float = 10,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> ImageCheck:
    """Fetch a bounded image, validating redirects, MIME, signature and content hash."""
    source = source_for_url(url)
    reason = image_delivery_rejection_reason(url)
    if reason:
        return ImageCheck(False, source=source, reason=reason)

    current_url = url
    headers = {
        "Accept": "image/avif,image/webp,image/png,image/jpeg;q=0.9,*/*;q=0.1",
    }
    async with httpx.AsyncClient(
        timeout=timeout_seconds, follow_redirects=False
    ) as client:
        for _ in range(4):
            if image_delivery_rejection_reason(current_url):
                return ImageCheck(
                    False,
                    source=source,
                    final_url=current_url,
                    reason="Redirecionamento para URL não autorizada.",
                )
            try:
                async with client.stream(
                    "GET", current_url, headers=headers
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            return ImageCheck(
                                False,
                                source=source,
                                status_code=response.status_code,
                                reason="Redirecionamento sem destino.",
                            )
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status_code not in {200, 206}:
                        return ImageCheck(
                            False,
                            source=source,
                            status_code=response.status_code,
                            final_url=current_url,
                            reason=f"A fonte respondeu HTTP {response.status_code}.",
                        )
                    content_type = (
                        response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .lower()
                    )
                    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                        return ImageCheck(
                            False,
                            source=source,
                            status_code=response.status_code,
                            final_url=current_url,
                            reason="A resposta não tem MIME de imagem compatível.",
                        )
                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            too_large = int(content_length) > max_bytes
                        except ValueError:
                            return ImageCheck(
                                False,
                                source=source,
                                status_code=response.status_code,
                                final_url=current_url,
                                reason="Tamanho informado pela fonte é inválido.",
                            )
                        if too_large:
                            return ImageCheck(
                                False,
                                source=source,
                                status_code=response.status_code,
                                final_url=current_url,
                                reason="Imagem excede o limite de tamanho.",
                            )
                    prefix = bytearray()
                    digest = sha256()
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            return ImageCheck(
                                False,
                                source=source,
                                status_code=response.status_code,
                                final_url=current_url,
                                reason="Imagem excede o limite de tamanho.",
                            )
                        if len(prefix) < 16:
                            prefix.extend(chunk[: 16 - len(prefix)])
                        digest.update(chunk)
                    data = bytes(prefix)
                    valid_signature = (
                        data.startswith(b"\xff\xd8\xff")
                        or data.startswith(b"\x89PNG\r\n\x1a\n")
                        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
                    )
                    if not valid_signature:
                        return ImageCheck(
                            False,
                            source=source,
                            status_code=response.status_code,
                            final_url=current_url,
                            reason=("Assinatura incompatível com uma imagem aceita."),
                        )
                    content_hash = digest.hexdigest()
                    return ImageCheck(
                        True,
                        source=source,
                        status_code=response.status_code,
                        final_url=current_url,
                        content_sha256=content_hash,
                    )
            except httpx.HTTPError as exc:
                return ImageCheck(
                    False,
                    source=source,
                    final_url=current_url,
                    reason=f"Falha de rede: {type(exc).__name__}.",
                )
    return ImageCheck(
        False, source=source, final_url=current_url, reason="Redirecionamentos demais."
    )
