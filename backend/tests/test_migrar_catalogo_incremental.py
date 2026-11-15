from scripts.migrar_catalogo_incremental import (
    image_source_authorized,
    normalized,
    parse_args,
    row_signature,
    valid_http_url,
)


def test_identity_normalization_only_collapses_case_and_whitespace():
    assert normalized("  ACETAMINOFENO   500 mg ") == "acetaminofeno 500 mg"
    assert normalized(None) == ""


def test_migration_only_accepts_http_urls_with_a_host():
    assert valid_http_url("https://farmacia.example/item")
    assert valid_http_url("http://farmacia.example/item")
    assert not valid_http_url("javascript:alert(1)")
    assert not valid_http_url("https:///missing-host")
    assert not valid_http_url(None)


def test_image_import_requires_a_source_with_reuse_cleared():
    assert image_source_authorized("Indiana")
    assert not image_source_authorized("Araujo")
    assert not image_source_authorized("Pacheco")


def test_source_row_signature_is_stable_and_tracks_all_requested_fields():
    fields = ("codigo_barras", "name_search")
    first = {"codigo_barras": "123", "name_search": "Produto"}
    second = {"name_search": "Produto", "codigo_barras": "123"}
    changed = {"codigo_barras": "123", "name_search": "Outro produto"}

    assert row_signature(first, fields) == row_signature(second, fields)
    assert row_signature(first, fields) != row_signature(changed, fields)


def test_cli_requires_an_explicit_environment_and_snapshot():
    args = parse_args(
        [
            "--environment",
            "staging",
            "--snapshot-id",
            "a" * 64,
            "--apply",
        ]
    )
    assert args.environment == "staging"
    assert args.apply is True
    assert args.batch_size == 200
