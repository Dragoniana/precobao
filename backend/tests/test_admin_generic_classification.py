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
