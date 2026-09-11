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
import csv
import json
import logging
import sys
from pathlib import Path

from config.settings import DEFAULT_TRIAL_ARCHIVE, RawData, TrialArchive
from dao.tsv_repository import TsvRepository
from service import adjudication, approval_map, identity_census
from utility.logging import configure

#: Every mode this script dispatches, with the one-line help argparse shows.
MODES: dict[str, str] = {
    "census": "print the drug-identity chain, date-shape census and "
    "application-number taxonomy measured from fda_raw/",
    "map": "build the trial -> approval mapping and write the TSV tables",
    "adjudicate-prepare": "write adjudication/units.jsonl, the section-14 "
    "excerpts for agents to judge (see docs/runbooks/adjudicate-section-14.md)",
    "adjudicate-record": "validate and append judged verdicts from --from",
    "adjudicate-combine": "pair the judges' answer files in adjudication/raw/ "
    "and report what still needs a tiebreak",
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
        "--limit",
        type=int,
        default=None,
        help="adjudicate-prepare: only emit this many undecided units (pilot)",
    )
    parser.add_argument(
        "--from",
        dest="from_file",
        default=None,
        help="adjudicate-record: the JSONL of judged units to validate and append",
    )
    parser.add_argument(
        "--adjudication",
        default="adjudication",
        help="directory holding units.jsonl and the curated verdicts.jsonl",
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
        verdicts = adjudication.read_verdicts(Path(args.adjudication) / "verdicts.jsonl")
        if verdicts:
            print(f"applying {len(verdicts):,} section-14 verdicts")
        report = approval_map.build(data, archive, verdicts)
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
            ("nct_id", "application", "evidence", "confidence", "has_results",
             "polarity"),
            report.links,
        )
        repository.save(
            "trial_approval_map",
            ("nct_id", "application", "app_type", "drug", "unii",
             "first_approval", "sponsor", "has_results", "confidence",
             "polarity"),
            report.mapping,
        )
        print(approval_map.render(report))
        print(f"\nwritten to {repository.out_dir}/")
        return 0

    if args.mode == "adjudicate-prepare":
        data = RawData(root=Path(args.fda_raw))
        if not data.exists:
            print(f"{data.root} not found")
            return 1
        out_dir = Path(args.adjudication)
        links = Path(args.out) / "links.tsv"
        if not links.is_file():
            print(f"{links} not found — run --mode map first")
            return 1
        with links.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        wanted = {(r["nct_id"], r["application"]) for r in rows}
        decided = adjudication.read_verdicts(out_dir / "verdicts.jsonl")
        units = [
            u for u in adjudication.extract_units(data, wanted)
            if u.unit_id not in decided
        ]
        chosen = adjudication.prioritise(units, args.limit)
        written = adjudication.write_units(chosen, out_dir / "units.jsonl")
        print(
            f"{len(wanted):,} links -> {len(units):,} undecided units; "
            f"wrote {written:,} to {out_dir / 'units.jsonl'}\n"
            f"next: follow docs/runbooks/adjudicate-section-14.md"
        )
        return 0

    if args.mode == "adjudicate-combine":
        raw = Path(args.adjudication) / "raw"
        tiebreaks: dict[str, str] = {}
        tie_file = raw / "tiebreaks.txt"
        if tie_file.is_file():
            tiebreaks = adjudication.read_judge_file(tie_file)
        agreed: list[dict[str, object]] = []
        pending: list[str] = []
        absent: list[str] = []
        for a_file in sorted(raw.glob("batch-*-A.txt")):
            b_file = raw / a_file.name.replace("-A.txt", "-B.txt")
            rows, needs, missing = adjudication.combine(
                adjudication.read_judge_file(a_file),
                adjudication.read_judge_file(b_file),
                tiebreaks,
            )
            agreed.extend(rows)
            pending.extend(needs)
            absent.extend(missing)
        out = Path(args.adjudication) / "judged.jsonl"
        with out.open("w", encoding="utf-8") as handle:
            for row in agreed:
                handle.write(json.dumps(row) + "\n")
        print(f"paired {len(agreed):,} units -> {out}")
        print(f"  needing a third judge  {len(pending):,}")
        print(f"  answered by one judge  {len(absent):,}")
        if pending:
            listing = Path(args.adjudication) / "tiebreak_needed.txt"
            listing.write_text("\n".join(pending) + "\n", encoding="utf-8")
            print(f"  -> {listing}")
        return 0

    if args.mode == "adjudicate-record":
        if not args.from_file:
            print("--from is required: the JSONL of judged units")
            return 1
        out_dir = Path(args.adjudication)
        judged = adjudication.iter_json_lines(Path(args.from_file))
        written, refused = adjudication.record_verdicts(judged, out_dir / "verdicts.jsonl")
        print(f"recorded {written:,} verdicts, refused {refused:,}")
        for name, count in adjudication.summarise(
            adjudication.read_verdicts(out_dir / "verdicts.jsonl")
        ):
            print(f"  {name:<20} {count:>7,}")
        return 1 if refused and not written else 0

    # argparse's choices= makes this unreachable; it is here so mypy sees a
    # return on every path rather than an implicit None.
    raise AssertionError(f"unhandled mode: {args.mode}")


if __name__ == "__main__":
    sys.exit(main())
