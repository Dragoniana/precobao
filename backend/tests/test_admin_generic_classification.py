from app.services.imagens import is_generic_image_url


def test_pacheco_generic_prescription_art_is_not_a_product_photo():
    assert is_generic_image_url(
        "https://drogariaspacheco.vteximg.com.br/arquivos/ids/1142298/"
        "genericos-tarja-vermelha-com-retencao-em-capsula-pacheco.jpg"
    )


def test_specific_generic_medicine_package_remains_a_product_photo():
    assert not is_generic_image_url(
        "https://drogariaspacheco.vteximg.com.br/arquivos/ids/123/"
        "celecoxibe-200mg-generico-eurofarma-10-capsulas.jpg"
    )


def test_araujo_provided_generic_art_assets_are_illustrative_fallbacks():
    assert is_generic_image_url(
        "https://www.araujo.com.br/on/demandware.static/Sites-Araujo-Site/"
        "-/default/dw1f93f88e/images/generico.webp"
    )
    assert is_generic_image_url(
        "https://www.araujo.com.br/on/demandware.static/Sites-Araujo-Site/"
        "-/default/dwf281a111/images/tarjado.webp"
    )
