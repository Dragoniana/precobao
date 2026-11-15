from datetime import UTC, datetime, timedelta

import httpx

from scripts.backfill_images_only import _retry_after, exact_product_images


def test_exact_product_images_keeps_only_secure_images_for_matching_product():
    payload = [
        {
            "productId": "701173",
            "items": [
                {
                    "images": [
                        {
                            "imageUrl": (
                                "//drogariaspacheco.vteximg.com.br/arquivos/ids/1/701173.jpg"
                            )
                        },
                        {
                            "imageUrl": "http://drogariaspacheco.vteximg.com.br/701173.jpg"
                        },
                        {"imageUrl": "https://example.invalid/other.jpg"},
                    ]
                }
            ],
        },
        {
            "productId": "999999",
            "items": [
                {
                    "images": [
                        {
                            "imageUrl": "https://drogariaspacheco.vteximg.com.br/999999.jpg"
                        }
                    ]
                }
            ],
        },
    ]

    assert exact_product_images(payload, "701173") == [
        "https://drogariaspacheco.vteximg.com.br/arquivos/ids/1/701173.jpg"
    ]
    assert exact_product_images(payload, "no-match") == []


def test_retry_after_respects_seconds_and_http_date():
    seconds = httpx.Response(429, headers={"Retry-After": "7"})
    assert _retry_after(seconds, 0) == 7

    retry_at = datetime.now(UTC) + timedelta(seconds=20)
    http_date = httpx.Response(
        503, headers={"Retry-After": retry_at.strftime("%a, %d %b %Y %H:%M:%S GMT")}
    )
    assert 1 <= _retry_after(http_date, 0) <= 20
