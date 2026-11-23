import base64
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from scripts.admin_image_import import import_item, initialize, rollback_transactions

engine = create_engine(os.environ["DATABASE_URL"])
run = "during-rollback-20261123"
with engine.begin() as c:
    initialize(c)
p = Path("/work/stage-sample2")
i = json.loads((p / "manifest.jsonl").read_text().splitlines()[0])
i["content_base64"] = base64.b64encode(
    (p / "blobs" / i["content_sha256"]).read_bytes()
).decode()
with Session(engine) as db:
    with db.begin():
        assert import_item(db, run, i).startswith(("INSERTED", "REPLACED"))
with engine.connect() as c:
    lid = c.execute(
        text("SELECT link_id FROM admin_image_import_journal WHERE run_id=:r"),
        {"r": run},
    ).scalar_one()
started = Event()


def reverse():
    started.set()
    with engine.connect() as c:
        return rollback_transactions(c, run)


with engine.begin() as updater:
    updater.execute(
        text(
            "UPDATE medicine_image_links "
            "SET source_image_url=source_image_url||'?during=1' WHERE id=:i"
        ),
        {"i": lid},
    )
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(reverse)
    assert started.wait(5)
    time.sleep(1)
    assert not future.done()
result = future.result(timeout=15)
pool.shutdown()
assert result == {"CONFLICT": 1}, result
with engine.connect() as c:
    assert (
        c.execute(
            text("SELECT source_image_url FROM medicine_image_links WHERE id=:i"),
            {"i": lid},
        )
        .scalar_one()
        .endswith("?during=1")
    )
print("CONCURRENT_UPDATE_DURING_ROLLBACK_PASSED")
