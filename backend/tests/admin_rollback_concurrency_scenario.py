import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import scripts.admin_image_import as mod

engine = create_engine(os.environ["DATABASE_URL"])
run = "rollback-failure-simultaneous-20261123"
with engine.begin() as c:
    mod.initialize(c)
p = Path("/work/stage-sample2")
items = []
for line in (p / "manifest.jsonl").read_text().splitlines()[:10]:
    i = json.loads(line)
    i["content_base64"] = base64.b64encode(
        (p / "blobs" / i["content_sha256"]).read_bytes()
    ).decode()
    items.append(i)
with Session(engine) as db:
    for i in items:
        with db.begin():
            mod.import_item(db, run, i)
original = mod.rollback
calls = 0


def interrupted(db, r, key=None):
    global calls
    calls += 1
    if calls == 3:
        raise RuntimeError("controlled_failure")
    return original(db, r, key)


mod.rollback = interrupted
with engine.connect() as c:
    try:
        mod.rollback_transactions(c, run)
    except RuntimeError:
        pass
    else:
        raise AssertionError("failure_not_injected")
    with c.begin():
        states = dict(
            c.execute(
                text(
                    "SELECT rollback_status,count(*) FROM admin_image_import_journal "
                    "WHERE run_id=:r GROUP BY rollback_status"
                ),
                {"r": run},
            ).all()
        )
    assert states.get("ROLLED_BACK") == 2 and states.get("FAILED") == 1, states
mod.rollback = original


def resume(_):
    with engine.connect() as c:
        return mod.rollback_transactions(c, run)


with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(resume, range(2)))
with engine.connect() as c:
    final = mod.rollback_transactions(c, run)
    assert set(final) == {"ROLLED_BACK"}, final
print(
    json.dumps(
        {
            "FAILURE_COMMITS_PRESERVED": states,
            "SIMULTANEOUS_RESUME": results,
            "FINAL": final,
        }
    )
)
