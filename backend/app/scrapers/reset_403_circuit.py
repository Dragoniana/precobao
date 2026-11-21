"""Explicit operator command to reopen a scraper stopped by HTTP 403."""

from __future__ import annotations

import argparse

from app.scrapers import araujo_scheduler, pacheco_scheduler
from app.scrapers.source_403_recovery import operator_reset_state


def reset(source: str) -> None:
    scheduler = araujo_scheduler if source == "araujo" else pacheco_scheduler
    state = scheduler._checkpoint()
    updated = operator_reset_state(state)
    scheduler._write_checkpoint(updated)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=("araujo", "pacheco"))
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="confirm access review and clear this source's HTTP 403 circuit",
    )
    args = parser.parse_args()
    if not args.confirm:
        parser.error("--confirm is required after operational review")
    try:
        reset(args.source)
    except ValueError as error:
        parser.error(str(error))
    print(f"{args.source} HTTP 403 circuit reset; checkpoint progress preserved.")


if __name__ == "__main__":
    main()
