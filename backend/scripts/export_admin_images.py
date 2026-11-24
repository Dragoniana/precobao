"""Export normalized archive bytes read-only into a deduplicated offline package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scripts.sync_development_images_to_production import (
    _load_scraped_image_candidates,
    _source_image_bytes,
)


def export_package(url: str, directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    blobs = directory / "blobs"
    blobs.mkdir(exist_ok=True)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.exec_driver_sql(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                with Session(bind=connection) as db:
                    candidates, counts = _load_scraped_image_candidates(db)
                    manifest = directory / "manifest.jsonl"
                    temporary = manifest.with_suffix(".tmp")
                    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                        for candidate in candidates:
                            content = _source_image_bytes(db, candidate)
                            target = blobs / candidate.content_sha256
                            if target.exists():
                                if (
                                    hashlib.sha256(target.read_bytes()).hexdigest()
                                    != target.name
                                ):
                                    raise ValueError("existing_export_blob_corrupt")
                            else:
                                target.write_bytes(content)
                            stream.write(
                                json.dumps(asdict(candidate), sort_keys=True) + "\n"
                            )
                    temporary.replace(manifest)
                    report = dict(counts)
                    report.update(
                        {
                            "format": "preco-bao.admin-images.v1",
                            "manifest_sha256": hashlib.sha256(
                                manifest.read_bytes()
                            ).hexdigest(),
                            "exported": len(candidates),
                            "unique_blobs": len({c.content_sha256 for c in candidates}),
                        }
                    )
                    (directory / "package.json").write_text(
                        json.dumps(report, indent=2), encoding="utf-8"
                    )
                    return report
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            export_package(os.environ["PRECOBAO_DEVELOPMENT_DATABASE_URL"], args.output)
        )
    )


if __name__ == "__main__":
    main()
