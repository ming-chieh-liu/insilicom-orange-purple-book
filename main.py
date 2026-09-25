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
from datetime import date
from pathlib import Path
from typing import Any

from config.settings import (
    DEFAULT_MESH_DESCRIPTORS,
    DEFAULT_MOIETY_EXTRACT,
    DEFAULT_TRIAL_ARCHIVE,
    BiokdeConnection,
    MeshVocabulary,
    RawData,
    TrialArchive,
)
from dao.biokde import BiokdeRepository, connection_uri
from dao.graph_evidence import GraphEvidenceRepository
from dao.tsv_repository import TsvRepository
from source import label_pdf, mesh_descriptors, moiety, review_docs, trial_studies
from source.raw_table import read_rows
from service import (
    adjudication,
    assemble,
    candidates,
    approval_events,
    approval_map,
    attribution,
    identity_census,
    identification,
    indication_normalize,
    indications,
    label_role,
    letter_locate,
    precision_check,
    letters,
    review_role,
    review_trials,
    graph_judging,
    graph_links,
    graph_trials,
    normalize,
    section14,
    validate,
)
from utility.logging import configure

#: Every mode this script dispatches, with the one-line help argparse shows.
MODES: dict[str, str] = {
    "census": "print the drug-identity chain, date-shape census and "
    "application-number taxonomy measured from fda_raw/",
    "map": "build the trial -> approval mapping and write the TSV tables",
    "indications": "code label section 1 to MeSH diseases and write "
    "indications.tsv and indication_text.tsv",
    "approval-events": "write approval_events.tsv, one row per FDA approval "
    "rather than one per application",
    "attribute": "check trial_approval_map.tsv against the approval events "
    "and write trial_approval_dated.tsv (needs --mode map first)",
    "adjudicate-prepare": "write adjudication/units.jsonl, the section-14 "
    "excerpts for agents to judge (see docs/runbooks/adjudicate-section-14.md)",
    "adjudicate-record": "validate and append judged verdicts from --from",
    "adjudicate-combine": "pair the judges' answer files in adjudication/raw/ "
    "and report what still needs a tiebreak",
    "normalize": "map FDA drugs and indications, and the trial side, onto "
    "MeSH descriptors via the Insilicom graph",
    "letters": "fetch each trial-relevant submission's approval letter, code the "
    "indication it states, and choose one indication source per submission (D25)",
    "reviews": "fetch each trial-relevant submission's FDA reviews, match the "
    "protocol ids they cite to registry trials, and write role units (D26)",
    "review-role-prepare": "batch the review-route units without a current-prompt "
    "verdict, calibration units first (see docs/runbooks/review-trial-role.md)",
    "review-role-combine": "pair the role judges' answers, batch tiebreaks, record "
    "decided units",
    "label-role-units": "write adjudication/label_role_units.jsonl: every label "
    "link P5 would make, with its own submission's section-14 passages (D28)",
    "label-role-prepare": "batch the label-link units without a current-prompt "
    "verdict (see docs/runbooks/label-trial-role.md)",
    "label-role-combine": "pair the label-role judges' answers, batch tiebreaks, "
    "record decided units",
    "graph-prepare": "batch the tier-C units without a current-prompt verdict "
    "(see docs/runbooks/select-graph-trial.md)",
    "graph-combine": "pair the tier-C judges' answers, batch tiebreaks, record "
    "decided units in adjudication/graph_verdicts.jsonl",
    "precision-prepare": "sample withheld-answer P4 units per evidence group into "
    "adjudication/precision_p4_v1/ (--limit = units per group, default 40)",
    "precision-combine": "pair the precision experiment's judge answers, batch "
    "tiebreaks, and print precision per evidence group",
    "letter-locate-prepare": "write locate units and batches for letters the "
    "keyword rule missed (see docs/runbooks/letter-indication.md)",
    "letter-indication-combine": "pair locator/verifier answers, extract agreed "
    "spans by code, record decided letters",
    "indication-normalize": "code every indication source (letter, label section 1, "
    "section-14 headings, openFDA fallback) into submission_indications.tsv (D30)",
    "labels": "fetch each trial-relevant submission's archived label and read "
    "its section 14 into trial descriptions",
    "candidates": "narrow the trial registry to a judgeable shortlist per "
    "section-14 description (needs --mode labels and --mode normalize first)",
    "identify-prepare": "write adjudication/identify_units.jsonl, the trial "
    "descriptions and their shortlists (see docs/runbooks/identify-trial.md)",
    "identify-combine": "pair the judges' answer files in "
    "adjudication/identify_raw/ and report what needs a tiebreak",
    "identify-record": "validate and append judged identifications from --from",
    "assemble": "join the units, the verdicts and the spine into "
    "trial_submission_map.tsv and unlinked_submissions.tsv",
    "validate": "score the map against the links the FDA states outright: "
    "shortlist recall now, judge precision with --limit",
    "graph-trials": "precompute the trials the literature connects to each "
    "application, as a ranking input for P3 (Mongo only, no agent calls)",
    "graph-links": "tier C: gather literature candidates from the Insilicom "
    "graph for approvals the label route could not serve",
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
        "--mesh",
        default=str(DEFAULT_MESH_DESCRIPTORS),
        help="MeSH descriptor XML, the disease vocabulary indications code to",
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
        help="adjudicate-prepare / labels: cap the units or submissions processed (pilot)",
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
        "--moiety",
        default=str(DEFAULT_MOIETY_EXTRACT),
        help="structured label extract, for salt -> active moiety",
    )
    parser.add_argument(
        "--label-cache",
        default="fda_raw/label_pdf",
        help="where archived label PDFs are cached (gitignored, rebuildable)",
    )
    parser.add_argument(
        "--letter-cache",
        default="fda_raw/letter_pdf",
        help="where approval-letter PDFs are cached (gitignored, rebuildable)",
    )
    parser.add_argument(
        "--review-cache",
        default="fda_raw/review_pdf",
        help="where FDA review documents are cached (gitignored, rebuildable)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="letters, labels, reviews: read only cached files; send no request to FDA",
    )
    parser.add_argument(
        "--parsers",
        type=int,
        default=16,
        help="labels: parallel PDF parsers (CPU-bound, so processes not threads)",
    )
    parser.add_argument(
        "--creds",
        default="creds.json",
        help="credentials file for the Insilicom graph (gitignored)",
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

    if args.mode == "indications":
        data = RawData(root=Path(args.fda_raw))
        if not data.exists:
            print(f"{data.root} not found — the indications need the bulk data tree")
            return 1
        vocabulary = MeshVocabulary(descriptors=Path(args.mesh))
        if not vocabulary.exists:
            print(f"{vocabulary.descriptors} not found — pass --mesh")
            return 1
        indication_report = indications.build(data, vocabulary)
        repository = TsvRepository(args.out)
        repository.save(
            "indications",
            ("application", "app_type", "drug", "unii", "mesh_id", "mesh_term",
             "evidence"),
            indication_report.rows,
        )
        repository.save(
            "indication_text",
            ("application", "effective_time", "n_diseases", "text"),
            indication_report.texts,
        )
        print(indications.render(indication_report))
        print(f"\nwritten to {repository.out_dir}/")
        return 0

    if args.mode == "approval-events":
        data = RawData(root=Path(args.fda_raw))
        if not data.exists:
            print(f"{data.root} not found — the events need the bulk data tree")
            return 1
        events_report = approval_events.build(data)
        repository = TsvRepository(args.out)
        repository.save(
            "approval_events",
            ("application", "app_type", "submission_type", "submission_no",
             "submission_class", "action_type", "approval_date", "era",
             "review_priority", "orphan", "indication_changing",
             "trial_relevant", "class_code", "trial_scope", "scope_basis"),
            events_report.rows,
        )
        print(approval_events.render(events_report))
        print(f"\nwritten to {repository.out_dir}/")
        return 0

    if args.mode == "graph-trials":
        data = RawData(root=Path(args.fda_raw))
        if not data.exists:
            print(f"{data.root} not found")
            return 1
        out_dir = Path(args.out)
        connection = BiokdeConnection(credentials=Path(args.creds))
        if not connection.has_credentials:
            print(f"{connection.credentials} not found — pass --creds")
            return 1
        gt_spine = approval_events.build(data)
        gt_apps = frozenset(
            row.application for row in gt_spine.rows if row.trial_relevant == "true"
        )
        gt_ingredients = normalize.ingredients_of(data, gt_apps)
        import pymongo

        gt_client: pymongo.MongoClient[dict[str, Any]] = pymongo.MongoClient(
            connection_uri(connection),
            serverSelectionTimeoutMS=connection.timeout_ms,
        )
        try:
            gt_report = graph_trials.build(
                out_dir,
                GraphEvidenceRepository(gt_client, connection),
                gt_apps,
                gt_ingredients,
            )
        finally:
            gt_client.close()
        repository_gt = TsvRepository(out_dir)
        repository_gt.save(
            "graph_trials", ("application", "nct_id", "mentions"), gt_report.rows
        )
        print(graph_trials.render(gt_report))
        return 0

    if args.mode == "graph-links":
        data = RawData(root=Path(args.fda_raw))
        if not data.exists:
            print(f"{data.root} not found — tier C needs the bulk data tree")
            return 1
        out_dir = Path(args.out)
        unlinked_path = out_dir / "unlinked_submissions.tsv"
        if not unlinked_path.is_file():
            print(f"{unlinked_path} not found — run --mode assemble first")
            return 1
        connection = BiokdeConnection(credentials=Path(args.creds))
        if not connection.has_credentials:
            print(f"{connection.credentials} not found — pass --creds")
            return 1
        # Exactly what the label route EXHAUSTED, read rather than recomputed.
        # not_yet_judged is excluded: that means P4 has not looked, not that it
        # failed, and a tier-C link is never promoted to the tier P4 might reach.
        with unlinked_path.open(encoding="utf-8") as handle:
            unserved = graph_links.exhausted_only(
                list(csv.DictReader(handle, delimiter="\t"))
            )
        archive = TrialArchive(root=Path(args.trial_archive))
        studies = {
            facts.nct_id: (facts.start_date, facts.enrollment, ";".join(facts.phases),
                           facts.primary_completion)
            for facts in trial_studies.read_study_facts(archive)
        }
        spine_rows = approval_events.build(data)
        ingredients = normalize.ingredients_of(
            data,
            frozenset(
                row.application
                for row in spine_rows.rows
                if row.trial_relevant == "true"
            ),
        )
        import pymongo

        graph_client: pymongo.MongoClient[dict[str, Any]] = pymongo.MongoClient(
            connection_uri(connection),
            serverSelectionTimeoutMS=connection.timeout_ms,
        )
        try:
            graph_report = graph_links.build(
                out_dir,
                GraphEvidenceRepository(graph_client, connection),
                studies,
                unserved,
                ingredients,
                limit=args.limit,
            )
        finally:
            graph_client.close()
        units_path = Path(args.adjudication) / "graph_units.jsonl"
        units_path.parent.mkdir(parents=True, exist_ok=True)
        with units_path.open("w", encoding="utf-8") as handle:
            for graph_unit in graph_report.units:
                handle.write(
                    json.dumps(
                        {
                            **graph_unit._asdict(),
                            "candidates": [
                                c._asdict() for c in graph_unit.candidates
                            ],
                        }
                    )
                    + "\n"
                )
        print(graph_links.render(graph_report))
        print(f"\nwritten to {units_path}")
        return 0

    if args.mode == "validate":
        out_dir = Path(args.out)
        units_path = out_dir / "candidates.jsonl"
        if not units_path.is_file():
            print(f"{units_path} not found — run --mode candidates first")
            return 1
        recall = validate.shortlist_recall(units_path)
        tiers = validate.tier_breakdown(out_dir / "trial_submission_map.tsv")
        print(validate.render(recall, tiers))
        routes, moved = validate.route_agreement(out_dir / "trial_submission_map.tsv")
        print("\n  ROUTES — which sources named each link (D26, D29)")
        for sources, count in routes:
            print(f"    {sources or '(none recorded)':<38}{count:>9,}")
        print(f"    attribution moved between routes       {moved:>9,}")
        if args.limit:
            adjudication_dir = Path(args.adjudication)
            truth = validate.withhold(
                units_path,
                adjudication_dir / "validate_units.jsonl",
                args.limit,
            )
            truth_path = adjudication_dir / "validate_truth.json"
            truth_path.write_text(json.dumps(truth, indent=2), encoding="utf-8")
            precision, _ = validate.score(
                truth, adjudication_dir / "identify_verdicts.jsonl"
            )
            print()
            print(f"  withheld-answer units written to {truth_path.parent}")
            print(f"    units                                  {len(truth):>9,}")
            print(f"    judged so far                          {precision.scored:>9,}")
            if precision.scored:
                print(
                    f"    correct                                "
                    f"{precision.correct:>9,}  ({100 * precision.rate:.1f}% of "
                    f"answers given)"
                )
                print(f"    wrong                                  {precision.wrong:>9,}")
                print(
                    f"    abstained                              "
                    f"{precision.abstained:>9,}  "
                    f"({100 * precision.abstention_rate:.1f}%)"
                )
        return 0

    if args.mode == "assemble":
        out_dir = Path(args.out)
        if not (out_dir / "candidates.jsonl").is_file():
            print(f"{out_dir / 'candidates.jsonl'} not found — run --mode candidates")
            return 1
        assemble_report = assemble.build(out_dir, Path(args.adjudication))
        repository = TsvRepository(args.out)
        repository.save(
            "trial_submission_map",
            ("nct_id", "application", "submission_type", "submission_no",
             "approval_date", "action_type", "indication_mesh", "indication_term",
             "drug_mesh", "trial_name", "label_enrollment", "registry_enrollment",
             "evidence_tier", "evidence_source", "count_check", "drug_basis", "injected",
             "sources", "conflict"),
            assemble_report.links,
        )
        repository.save(
            "review_trial_context",
            ("nct_id", "application", "submission_type", "submission_no",
             "protocol_id", "role"),
            assemble_report.review_context,
        )
        repository.save(
            "unlinked_submissions",
            ("application", "submission_type", "submission_no", "approval_date",
             "trial_name", "reason"),
            assemble_report.unlinked,
        )
        print(assemble.render(assemble_report))
        print(f"\nwritten to {repository.out_dir}/")
        return 0

    if args.mode == "identify-prepare":
        out_dir = Path(args.out)
        adjudication_dir = Path(args.adjudication)
        units_source = out_dir / "candidates.jsonl"
        if not units_source.is_file():
            print(f"{units_source} not found — run --mode candidates first")
            return 1
        id_decided = identification.read_verdicts(
            adjudication_dir / "identify_verdicts.jsonl"
        )
        id_borrowed = identification.borrowed_ids(out_dir / "drug_map.tsv")
        id_guard = identification.guard_submissions(out_dir / "approval_events.tsv")
        id_prepare_pending = [
            unit
            for unit in identification.read_units(units_source, id_borrowed, id_guard)
            if unit["unit_id"] not in id_decided
        ]
        id_chosen = identification.prioritise(id_prepare_pending, args.limit)
        id_written = identification.write_units(
            id_chosen, adjudication_dir / "identify_units.jsonl"
        )
        print(
            f"{len(id_prepare_pending):,} undecided units; wrote {id_written:,} to "
            f"{adjudication_dir / 'identify_units.jsonl'}\n"
            f"next: follow docs/runbooks/identify-trial.md"
        )
        return 0

    if args.mode == "identify-combine":
        id_raw = Path(args.adjudication) / "identify_raw"
        id_tiebreaks: dict[str, str] = {}
        id_tie_file = id_raw / "id_tiebreaks.txt"
        if id_tie_file.is_file():
            id_tiebreaks = identification.read_judge_file(id_tie_file)
        id_agreed: list[dict[str, object]] = []
        id_pending: list[str] = []
        id_absent: list[str] = []
        for id_a_file in sorted(id_raw.glob("batch-*-A.txt")):
            id_b_file = id_raw / id_a_file.name.replace("-A.txt", "-B.txt")
            id_rows, id_needs, id_missing = identification.combine(
                identification.read_judge_file(id_a_file),
                identification.read_judge_file(id_b_file),
                id_tiebreaks,
            )
            id_agreed.extend(id_rows)
            id_pending.extend(id_needs)
            id_absent.extend(id_missing)
        id_out = Path(args.adjudication) / "identify_judged.jsonl"
        with id_out.open("w", encoding="utf-8") as id_handle:
            for id_row in id_agreed:
                id_handle.write(json.dumps(id_row) + "\n")
        print(f"paired {len(id_agreed):,} units -> {id_out}")
        print(f"  needing a third judge  {len(id_pending):,}")
        print(f"  answered by one judge  {len(id_absent):,}")
        if id_pending:
            id_listing = Path(args.adjudication) / "identify_tiebreak_needed.txt"
            id_listing.write_text("\n".join(id_pending) + "\n", encoding="utf-8")
            print(f"  -> {id_listing}")
        return 0

    if args.mode == "identify-record":
        if not args.from_file:
            print("--from is required: the JSONL of judged units")
            return 1
        adjudication_dir = Path(args.adjudication)
        allowed = identification.candidate_ids_by_unit(
            adjudication_dir / "identify_units.jsonl"
        )
        judged = identification.iter_json_lines(Path(args.from_file))
        id_rec_written, id_rec_refused = identification.record_verdicts(
            judged, adjudication_dir / "identify_verdicts.jsonl", allowed
        )
        print(f"recorded {id_rec_written:,} verdicts, refused {id_rec_refused:,}")
        for name, count in identification.summarise(
            identification.read_verdicts(
                adjudication_dir / "identify_verdicts.jsonl"
            )
        ):
            print(f"  {name:<20} {count:>7,}")
        return 1 if id_rec_refused and not id_rec_written else 0

    if args.mode == "candidates":
        data = RawData(root=Path(args.fda_raw))
        if not data.exists:
            print(f"{data.root} not found — candidates need the bulk data tree")
            return 1
        archive = TrialArchive(root=Path(args.trial_archive))
        if not archive.study_shards:
            print(f"{archive.root} has no {archive.study_glob}")
            return 1
        out_dir = Path(args.out)
        for needed in ("label_trials.tsv", "trial_drug_map.tsv", "drug_map.tsv"):
            if not (out_dir / needed).is_file():
                print(f"{out_dir / needed} not found — run labels and normalize first")
                return 1
        candidates_report = candidates.build(data, archive, out_dir)
        units_path = out_dir / "candidates.jsonl"
        with units_path.open("w", encoding="utf-8") as handle:
            for unit in candidates_report.units:
                handle.write(
                    json.dumps(
                        {
                            **unit._asdict(),
                            "candidates": [c._asdict() for c in unit.candidates],
                        }
                    )
                    + "\n"
                )
        print(candidates.render(candidates_report))
        print(f"\nwritten to {units_path}")
        return 0

    if args.mode == "letters":
        data = RawData(root=Path(args.fda_raw))
        if not data.exists:
            print(f"{data.root} not found — the letters need the bulk data tree")
            return 1
        letters_report = letters.build(
            data,
            Path(args.letter_cache),
            parsers=args.parsers,
            offline=args.offline,
        )
        repository = TsvRepository(args.out)
        repository.save(
            "letter_indications",
            ("application", "submission_type", "submission_no", "approval_date",
             "letter_url", "url_guessed", "shared_letter", "status", "sentence",
             "indication_bearing"),
            letters_report.rows,
        )
        print(letters.render(letters_report))
        print(f"\nwritten to {repository.out_dir}/")
        return 0

    if args.mode == "reviews":
        data = RawData(root=Path(args.fda_raw))
        if not data.exists:
            print(f"{data.root} not found — the reviews need the bulk data tree")
            return 1
        archive = TrialArchive(root=Path(args.trial_archive))
        if not archive.study_shards:
            print(f"{archive.root} has no {archive.study_glob}")
            return 1
        in_scope = [
            row for row in approval_events.build(data).rows if row.trial_relevant == "true"
        ]
        rv_wanted = {
            f"{r.application[-6:]}|{r.submission_type}|{r.submission_no}" for r in in_scope
        }
        documents = [
            d for d in review_docs.read_review_documents(data)
            if f"{d.application.zfill(6)}|{d.submission_type}|{d.submission_no}" in rv_wanted
        ]
        review_texts, fetch_stats = review_trials.collect_texts(
            documents, Path(args.review_cache), offline=args.offline, parsers=args.parsers
        )
        applicants = {
            (r.get("ApplNo") or "").strip().zfill(6): (r.get("SponsorName") or "").strip()
            for r in read_rows(data.applications)
        }
        drug_names: dict[str, set[str]] = {}
        brand_names: dict[str, set[str]] = {}
        for product in read_rows(data.products):
            digits = (product.get("ApplNo") or "").strip().zfill(6)
            brand_names.setdefault(digits, set()).add((product.get("DrugName") or "").strip())
            for part in [product.get("DrugName") or "", *(product.get("ActiveIngredient") or "").split(";")]:
                word = part.strip().upper().split(" ")[0] if part.strip() else ""
                if len(word) >= 5:
                    drug_names.setdefault(digits, set()).add(word)
        sentences = label_role.read_sentences(Path(args.out))
        rv_report = review_trials.build(
            [(r.application, r.submission_type, r.submission_no, r.approval_date) for r in in_scope],
            review_texts,
            review_trials.build_index(trial_studies.read_study_facts(archive)),
            applicants=applicants,
            drug_names=drug_names,
            brands=brand_names,
            approval_sentences=sentences,
        )
        repository = TsvRepository(args.out)
        repository.save(
            "review_trial_mentions",
            ("application", "submission_type", "submission_no", "approval_date",
             "document", "heading", "protocol_id", "nct_id", "outcome", "acronym"),
            rv_report.mentions,
        )
        units_path = Path(args.adjudication) / "review_role_units.jsonl"
        with units_path.open("w", encoding="utf-8") as handle:
            for rv_unit in rv_report.units:
                handle.write(json.dumps(rv_unit) + "\n")
        print(review_trials.render(rv_report, fetch_stats))
        print(f"\nwritten to {repository.out_dir}/ and {units_path}")
        return 0

    if args.mode in ("review-role-prepare", "review-role-combine"):
        adjudication_dir = Path(args.adjudication)
        version = f"v{review_role.PROMPT_VERSION}"
        role_units = adjudication_dir / "review_role_units.jsonl"
        role_batches = adjudication_dir / "review_role_batches" / version
        role_verdicts = adjudication_dir / "review_role_verdicts.jsonl"
        if args.mode == "review-role-prepare":
            role_report = review_role.prepare(role_units, role_batches, role_verdicts,
                                              limit=args.limit)
        else:
            role_report = review_role.combine(
                role_units, adjudication_dir / "review_role_raw" / version, role_batches,
                role_verdicts, decided_on=date.today().isoformat(),
            )
        print(review_role.render(role_report))
        return 0

    if args.mode == "label-role-units":
        data = RawData(root=Path(args.fda_raw))
        if not data.exists:
            print(f"{data.root} not found — the label links need the bulk data tree")
            return 1
        adjudication_dir = Path(args.adjudication)
        unfiltered = assemble.build(Path(args.out), adjudication_dir, apply_label_roles=False)
        lr_targets = label_role.targets_of(unfiltered.links)
        lr_sections = label_role.read_sections(
            label_role.label_urls(data, {t.key for t in lr_targets}),
            Path(args.label_cache), parsers=args.parsers,
        )
        lr_brands: dict[str, set[str]] = {}
        for product in read_rows(data.products):
            lr_brands.setdefault((product.get("ApplNo") or "").strip().zfill(6), set()).add(
                (product.get("DrugName") or "").strip()
            )
        lr_sentences = label_role.read_sentences(Path(args.out))
        lr_dates = {
            f"{l.application}|{l.submission_type}|{l.submission_no}": l.approval_date
            for l in unfiltered.links
        }
        lr_approvals = {
            key: label_role.approval_line(key, when, lr_brands.get(key.split("|")[0][-6:], set()),
                                          lr_sentences.get(key, ""))
            for key, when in lr_dates.items()
        }
        lr_report = label_role.build_units(lr_targets, lr_sections, lr_approvals)
        lr_path = adjudication_dir / "label_role_units.jsonl"
        with lr_path.open("w", encoding="utf-8") as handle:
            for lr_unit in lr_report.units:
                handle.write(json.dumps(lr_unit) + "\n")
        print(label_role.render_units(lr_report, lr_path))
        return 0

    if args.mode in ("label-role-prepare", "label-role-combine"):
        adjudication_dir = Path(args.adjudication)
        lr_version = f"v{label_role.PROMPT_VERSION}"
        lr_units = adjudication_dir / "label_role_units.jsonl"
        lr_batches = adjudication_dir / "label_role_batches" / lr_version
        lr_verdicts = adjudication_dir / "label_role_verdicts.jsonl"
        if args.mode == "label-role-prepare":
            lr_role_report = label_role.prepare(lr_units, lr_batches, lr_verdicts,
                                                limit=args.limit)
        else:
            lr_role_report = label_role.combine(
                lr_units, adjudication_dir / "label_role_raw" / lr_version, lr_batches,
                lr_verdicts, decided_on=date.today().isoformat(),
            )
        print(label_role.render(lr_role_report))
        return 0

    if args.mode in ("graph-prepare", "graph-combine"):
        adjudication_dir = Path(args.adjudication)
        gj_version = f"v{graph_judging.PROMPT_VERSION}"
        gj_units = adjudication_dir / "graph_units.jsonl"
        gj_batches = adjudication_dir / "graph_batches" / gj_version
        gj_verdicts = adjudication_dir / "graph_verdicts.jsonl"
        if args.mode == "graph-prepare":
            gj_report = graph_judging.prepare(gj_units, gj_batches, gj_verdicts,
                                              limit=args.limit)
        else:
            gj_report = graph_judging.combine(
                gj_units, adjudication_dir / "graph_raw" / gj_version, gj_batches,
                gj_verdicts, decided_on=date.today().isoformat(),
            )
        print(graph_judging.render(gj_report))
        return 0

    if args.mode in ("precision-prepare", "precision-combine"):
        pc_folder = Path(args.adjudication) / "precision_p4_v1"
        pc_units = Path(args.out) / "candidates.jsonl"
        if args.mode == "precision-prepare":
            pc_count, pc_batches = precision_check.prepare(pc_units, pc_folder, args.limit or 40)
            print(f"{pc_count} units, {len(pc_batches)} batches in {pc_folder}/batches; "
                  f"answers go to {pc_folder}/raw/prec-batch-NNN-{{A,B}}.txt")
        else:
            pc_new, pc_pending, pc_missing = precision_check.combine(
                pc_folder, date.today().isoformat())
            print(f"newly recorded {pc_new}; awaiting a tiebreak {len(pc_pending)}; "
                  f"answered by one judge only {len(pc_missing)}")
        print(precision_check.render(precision_check.score(pc_folder)
                                     if (pc_folder / "truth.json").exists() else (),
                                     precision_check.eligible_counts(pc_units)))
        return 0

    if args.mode == "letter-locate-prepare":
        data = RawData(root=Path(args.fda_raw))
        letters_tsv = Path(args.out) / "letter_indications.tsv"
        if not letters_tsv.is_file():
            print(f"{letters_tsv} not found — run --mode letters first")
            return 1
        with letters_tsv.open(encoding="utf-8") as handle:
            letter_rows = list(csv.DictReader(handle, delimiter="\t"))
        brands: dict[str, set[str]] = {}
        for product in read_rows(data.products):
            brands.setdefault((product.get("ApplNo") or "").strip().zfill(6), set()).add(
                (product.get("DrugName") or "").strip()
            )
        cache = Path(args.letter_cache)

        def _letter_text(url: str) -> str | None:
            if not url or not label_pdf.is_cached(url, cache):
                return None
            got = label_pdf.extract_text(label_pdf.cache_path(url, cache))
            return got[0] if got else None

        adjudication_dir = Path(args.adjudication)
        lp_report = letter_locate.prepare(
            letter_rows,
            _letter_text,
            brands,
            adjudication_dir / "letter_locate_units.jsonl",
            adjudication_dir / "letter_indication_batches",
            limit=args.limit,
        )
        print(
            f"{lp_report.candidates:,} letters the keyword rule missed; "
            f"{lp_report.units:,} units written ({lp_report.unreadable:,} unreadable); "
            f"{len(lp_report.new_batches)} new locate batches"
        )
        for path in lp_report.new_batches:
            print(f"  run locators A and B on {path.name}")
        print("next: follow docs/runbooks/letter-indication.md")
        return 0

    if args.mode == "letter-indication-combine":
        adjudication_dir = Path(args.adjudication)
        lc_report = letter_locate.combine(
            adjudication_dir / "letter_locate_units.jsonl",
            adjudication_dir / "letter_indication_raw",
            adjudication_dir / "letter_indication_batches",
            adjudication_dir / "letter_verify_units.jsonl",
            adjudication_dir / "letter_indication_verdicts.jsonl",
            decided=date.today().isoformat(),
        )
        print(letter_locate.render(lc_report))
        return 0

    if args.mode == "indication-normalize":
        data = RawData(root=Path(args.fda_raw))
        if not data.exists:
            print(f"{data.root} not found — the step needs the bulk data tree")
            return 1
        vocabulary = MeshVocabulary(descriptors=Path(args.mesh))
        if not vocabulary.exists:
            print(f"{vocabulary.descriptors} not found — pass --mesh")
            return 1
        connection = BiokdeConnection(credentials=Path(args.creds))
        if not connection.has_credentials:
            print(f"{connection.credentials} not found — pass --creds")
            return 1
        out_dir = Path(args.out)
        in_scope = [
            row for row in approval_events.build(data).rows if row.trial_relevant == "true"
        ]
        keys = [(r.application, r.submission_type, r.submission_no) for r in in_scope]

        def _tsv(name: str) -> list[dict[str, str]]:
            path = out_dir / name
            if not path.is_file():
                return []
            with path.open(encoding="utf-8") as handle:
                return list(csv.DictReader(handle, delimiter="\t"))

        def _key(row: dict[str, str]) -> str:
            return f"{row['application']}|{row['submission_type']}|{row['submission_no']}"

        letter_text = {
            _key(r): r["sentence"] for r in _tsv("letter_indications.tsv")
            if r.get("indication_bearing") == "true" and r.get("sentence")
        }
        label_text = {_key(r): r.get("text") or "" for r in _tsv("submission_indication_text.tsv")}
        located_text = letter_locate.accepted_spans(
            Path(args.adjudication) / "letter_indication_verdicts.jsonl"
        )
        headings: dict[str, list[str]] = {}
        for r in _tsv("label_trials.tsv"):
            heading = (r.get("heading") or "").strip()
            if heading and heading not in headings.setdefault(_key(r), []):
                headings[_key(r)].append(heading)
        openfda: dict[str, set[str]] = {}
        for r in _tsv("indications.tsv"):
            if r.get("mesh_id"):
                openfda.setdefault(r["application"], set()).add(r["mesh_id"])
        condition_rows = _tsv("trial_condition_map.tsv")
        exact = {r["source_name"].casefold(): r["mesh_id"] for r in condition_rows}
        known = {r["mesh_id"]: r["biokdeid"] for r in condition_rows if r.get("biokdeid")}
        import pymongo

        in_client: pymongo.MongoClient[dict[str, Any]] = pymongo.MongoClient(
            connection_uri(connection),
            serverSelectionTimeoutMS=connection.timeout_ms,
        )
        try:
            in_report = indication_normalize.build(
                keys,
                letters=letter_text,
                label_section_1=label_text,
                letters_located=located_text,
                headings=headings,
                openfda=openfda,
                exact=exact,
                known_biokdeids=known,
                index=indications.build_index(vocabulary),
                internal_ids=BiokdeRepository(in_client, connection).internal_ids,
            )
        finally:
            in_client.close()
        repository = TsvRepository(args.out)
        repository.save(
            "submission_indications",
            ("application", "submission_type", "submission_no", "source",
             "source_key", "text", "mesh_id", "biokdeid", "term", "chosen"),
            in_report.rows,
        )
        print(indication_normalize.render(in_report, len(keys)))
        print(f"\nwritten to {repository.out_dir}/")
        return 0

    if args.mode == "labels":
        data = RawData(root=Path(args.fda_raw))
        if not data.exists:
            print(f"{data.root} not found — the labels need the bulk data tree")
            return 1
        labels_report = section14.build(
            data,
            Path(args.label_cache),
            limit=args.limit,
            parsers=args.parsers,
            offline=args.offline,
        )
        repository = TsvRepository(args.out)
        repository.save(
            "label_trials",
            ("application", "submission_type", "submission_no", "section",
             "heading", "nct_id", "acronym", "enrollment", "design", "excerpt",
             "label_sha"),
            labels_report.descriptions,
        )
        repository.save(
            "submission_indication_text",
            ("application", "submission_type", "submission_no", "text"),
            labels_report.indications,
        )
        print(section14.render(labels_report))
        print(f"\nwritten to {repository.out_dir}/")
        return 0

    if args.mode == "normalize":
        data = RawData(root=Path(args.fda_raw))
        if not data.exists:
            print(f"{data.root} not found — normalisation needs the bulk data tree")
            return 1
        connection = BiokdeConnection(credentials=Path(args.creds))
        if not connection.has_credentials:
            print(f"{connection.credentials} not found — pass --creds")
            return 1
        vocabulary = MeshVocabulary(descriptors=Path(args.mesh))
        if not vocabulary.exists:
            print(f"{vocabulary.descriptors} not found — pass --mesh")
            return 1
        indications_tsv = Path(args.out) / "indications.tsv"
        if not indications_tsv.is_file():
            print(f"{indications_tsv} not found — run --mode indications first")
            return 1
        with indications_tsv.open(encoding="utf-8") as handle:
            indication_ids = [
                row["mesh_id"]
                for row in csv.DictReader(handle, delimiter="\t")
                if row.get("mesh_id")
            ]
        # One client per process: MongoClient is the pool. It is built here and
        # injected, so nothing under service/ knows a host or a credential.
        import pymongo

        client: pymongo.MongoClient[dict[str, Any]] = pymongo.MongoClient(
            connection_uri(connection),
            serverSelectionTimeoutMS=connection.timeout_ms,
        )
        try:
            graph = BiokdeRepository(client, connection)
            concepts, descriptor_names = mesh_descriptors.rollup_and_names(
                vocabulary
            )
            archive = TrialArchive(root=Path(args.trial_archive))
            folded = moiety.read(Path(args.moiety))
            normalize_report = normalize.build(
                data,
                archive,
                graph,
                concepts,
                indication_ids,
                folded,
                descriptor_names,
            )
        finally:
            client.close()
        repository_out = TsvRepository(args.out)
        # Named so a reader cannot mistake one column for another: the row
        # holds three different identifiers plus two names, and an empty
        # official_name once made 11542 look like the entity's name.
        columns = (
            "source_name",      # the string the source wrote
            "mesh_id",          # external vocabulary id: MESH:/PubChem:/...
            "biokdeid",         # the graph's INTERNAL node id, for edges
            "preferred_name",   # the entity's canonical name
            "entity_type",
            "basis",
        )
        repository_out.save("drug_map", columns, normalize_report.drugs)
        repository_out.save("indication_map", columns, normalize_report.indications)
        repository_out.save("trial_drug_map", columns, normalize_report.trial_drugs)
        repository_out.save(
            "trial_condition_map", columns, normalize_report.trial_conditions
        )
        print(normalize.render(normalize_report))
        print(f"\nwritten to {repository_out.out_dir}/")
        return 0

    if args.mode == "attribute":
        data = RawData(root=Path(args.fda_raw))
        if not data.exists:
            print(f"{data.root} not found — attribution needs the bulk data tree")
            return 1
        mapping = Path(args.out) / "trial_approval_map.tsv"
        if not mapping.is_file():
            print(f"{mapping} not found — run --mode map first")
            return 1
        archive = TrialArchive(root=Path(args.trial_archive))
        if not archive.study_shards:
            print(
                f"{archive.root} has no {archive.study_glob} — attribution needs "
                "study start dates, which are not in the manifest"
            )
            return 1
        with mapping.open(encoding="utf-8") as handle:
            map_rows = list(csv.DictReader(handle, delimiter="\t"))
        events = approval_events.indication_changing_dates(
            approval_events.build(data, types=None)
        )
        starts = trial_studies.read_start_dates(archive)
        attribution_report = attribution.attribute(map_rows, events, starts)
        repository = TsvRepository(args.out)
        repository.save(
            "trial_approval_dated",
            ("nct_id", "application", "app_type", "drug", "unii", "sponsor",
             "has_results", "polarity", "stamped_approval", "trial_start",
             "attribution", "approval_date", "candidate_approvals"),
            attribution_report.rows,
        )
        print(attribution.render(attribution_report))
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
