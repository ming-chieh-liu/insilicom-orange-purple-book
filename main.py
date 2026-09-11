"""The single entry script. Wiring only — no business logic lives here.

Each ``--mode`` parses arguments, builds the configuration, constructs the
service that does the work, and prints what it returns. Anything that decides
something belongs under ``service/``; anything that reads a file belongs under
``source/``; anything that parses a field belongs under ``utility/``.

Modes:

``census``
    Read ``fda_raw/`` and print the drug-identity chain, the date-shape census
    and the application-number taxonomy. Stores nothing and emits no tables — it
    exists to prove the parsers against the real corpus, and its numbers are the
    ones ``docs/drug-identity.md`` quotes.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from config.settings import DEFAULT_TRIAL_ARCHIVE, RawData, TrialArchive
from dao.tsv_repository import TsvRepository
from service import approval_map, identity_census
from utility.logging import configure

#: Every mode this script dispatches, with the one-line help argparse shows.
MODES: dict[str, str] = {
    "census": "print the drug-identity chain, date-shape census and "
    "application-number taxonomy measured from fda_raw/",
    "map": "build the trial -> approval mapping and write the TSV tables",
}


def build_parser() -> argparse.ArgumentParser:
    """The command line, declared in one place."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="FDA approval-to-trial mapping, built from FDA bulk data.",
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=sorted(MODES),
        help="; ".join(f"{name}: {help_}" for name, help_ in sorted(MODES.items())),
    )
    parser.add_argument(
        "--fda-raw",
        default="fda_raw",
        help="path to the FDA bulk data tree (default: fda_raw)",
    )
    parser.add_argument(
        "--trial-archive",
        default=str(DEFAULT_TRIAL_ARCHIVE),
        help="ClinicalTrials.gov archive directory, used to validate NCT ids",
    )
    parser.add_argument(
        "--out",
        default="output",
        help="directory the TSV tables are written to (default: output)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="log at DEBUG rather than INFO",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch one mode and return its exit status."""
    args = build_parser().parse_args(argv)
    configure(logging.DEBUG if args.verbose else logging.INFO)

    if args.mode == "census":
        data = RawData(root=Path(args.fda_raw))
        if not data.exists:
            print(f"{data.root} not found — the census needs the bulk data tree")
            return 1
        print(identity_census.render(identity_census.run(data)))
        return 0

    if args.mode == "map":
        data = RawData(root=Path(args.fda_raw))
        if not data.exists:
            print(f"{data.root} not found — the mapping needs the bulk data tree")
            return 1
        archive = TrialArchive(root=Path(args.trial_archive))
        report = approval_map.build(data, archive)
        # The repository is constructed here and injected: main is the only
        # place that decides where output goes.
        repository = TsvRepository(args.out)
        repository.save("drugs", ("unii", "name"), report.drugs)
        repository.save(
            "approvals",
            ("application", "app_type", "sponsor", "first_approval", "approved_pre_1982"),
            report.approvals,
        )
        repository.save(
            "links",
            ("nct_id", "application", "evidence", "confidence", "has_results"),
            report.links,
        )
        repository.save(
            "trial_approval_map",
            ("nct_id", "application", "app_type", "drug", "unii",
             "first_approval", "sponsor", "has_results", "confidence"),
            report.mapping,
        )
        print(approval_map.render(report))
        print(f"\nwritten to {repository.out_dir}/")
        return 0

    # argparse's choices= makes this unreachable; it is here so mypy sees a
    # return on every path rather than an implicit None.
    raise AssertionError(f"unhandled mode: {args.mode}")


if __name__ == "__main__":
    sys.exit(main())
