"""Where the FDA bulk data sits, and how each file is shaped. No logic.

Built once in ``main`` and injected into whatever needs it, so nothing below
this module has to know a path or guess a delimiter. Every field here is a
property of the snapshot on disk, measured 2026-09-11 — if a newer download
changes a member name or a row count, this file is the one place it changes.

The row counts are carried deliberately. They are what the reader's field-count
guard and the census's assertions check against, which is how a silently
truncated or re-formatted download gets caught instead of quietly producing
smaller numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: Tab, for the Drugs@FDA raw tables and the UNII names list.
TAB: str = "\t"

#: Tilde, for the three Orange Book text files. Verified never to appear
#: inside a value: every row has exactly its declared field count.
TILDE: str = "~"


@dataclass(frozen=True)
class DelimitedFile:
    """One delimited table, wherever it lives.

    ``member`` is set when the file is inside a zip and read from it directly;
    ``path`` is then the zip. ``fields`` is the declared column count, which the
    reader uses to reject a ragged row rather than mis-align it.
    """

    path: Path
    delimiter: str
    fields: int
    rows: int
    member: str | None = None


@dataclass(frozen=True)
class RawData:
    """The ``fda_raw/`` tree: what is in it, and what shape each file is."""

    root: Path

    #: Snapshot identities, so a stale download is visible rather than assumed.
    purplebook_snapshot: str = "purplebook-2026-08.csv"
    unii_snapshot: str = "UNII_Names_4Aug2026.txt"

    #: The year two-digit dates are resolved against. Matches the snapshots
    #: above and ``utility.fda_date.SNAPSHOT_YEAR``; not read from the clock.
    snapshot_year: int = 2026

    #: The Drugs@FDA raw tables, inside ``drugsatfda.zip``. Tab-delimited,
    #: CRLF, header present, no quoting. Authoritative over the openFDA JSON,
    #: which is missing application 206627.
    drugsfda_zip: Path = field(init=False)
    applications: DelimitedFile = field(init=False)
    products: DelimitedFile = field(init=False)
    submissions: DelimitedFile = field(init=False)
    #: ``SubmissionClassCodeID`` → description. Needed to tell an Efficacy
    #: supplement, which can change an indication, from a Labeling or CMC one,
    #: which cannot. The code ids are not stable across releases; the
    #: descriptions in this table are what the corpus states.
    submission_class_lookup: DelimitedFile = field(init=False)
    #: ``ActionTypes_LookupID`` → description. This is the table that says what
    #: an Efficacy supplement actually *did*: ``Efficacy-New Indication``,
    #: ``Efficacy-Accelerated Approval``, ``Efficacy-Accelerated Approval
    #: Confirmatory Study``, as against a new dosing regimen or a pediatric
    #: extrapolation. Accelerated status and its confirmatory conversion are
    #: therefore published fields, not something to infer from a PDF.
    action_types_lookup: DelimitedFile = field(init=False)
    #: Joins that lookup to ``(ApplNo, SubmissionType, SubmissionNo)``. Note the
    #: column order is ``SubmissionType`` first, not ``ApplNo``.
    submission_action_types: DelimitedFile = field(init=False)
    #: Per-submission properties. In this snapshot the only non-null code is
    #: ``Orphan`` (3,609 rows); everything else is ``Null``.
    submission_property_type: DelimitedFile = field(init=False)
    #: Per-submission documents. ``ApplicationDocsTypeID`` 1 is the approval
    #: letter (36,970 submissions) and **2 is the label as approved at that
    #: submission** (29,788) — the point-in-time label this project needs.
    #: The URLs are ``http://``; they must be requested over ``https://`` with
    #: redirects followed or they return nothing.
    application_docs: DelimitedFile = field(init=False)

    #: The Orange Book text files, already unzipped under ``orangebook/x/``.
    #: Tilde-delimited, CRLF, header present. ``products.txt`` has **no
    #: trailing newline**, so 48,664 data rows is correct and ``wc -l``
    #: undercounts by one.
    ob_products: DelimitedFile = field(init=False)
    ob_patent: DelimitedFile = field(init=False)
    ob_exclusivity: DelimitedFile = field(init=False)

    #: The openFDA label download: 14 zips, 262,757 records. Section 14
    #: (``clinical_studies``) is on 46,810 of them (17.8%) and is the only
    #: place the FDA names the trials behind an approval.
    label_shards: tuple[Path, ...] = field(init=False)
    label_records: int = 262_757

    #: The openFDA NDC download. Its ``openfda.spl_set_id`` is the bridge from
    #: a label to an application when the label's own ``openfda`` block is
    #: empty — 96,752 of its 137,804 records carry both.
    ndc_json: Path = field(init=False)
    ndc_records: int = 137_804

    #: The FDA's UNII Records (``UNII_Data.zip``: UNII -> PubChem CID), and NLM's
    #: MeSH supplementary concept records — both public downloads, for the
    #: exact UNII bridge (D39). Not part of the Drugs@FDA tree's checks.
    unii_records: Path = field(init=False)
    mesh_supplementary: Path = field(init=False)
    #: The UNII registry, inside ``UNIIs.zip``. Tab-delimited and **LF-only**,
    #: unlike every FDA file above. 1,050,022 data rows; line 1 is the header.
    #: Names are stored mixed case, so matching must case-fold.
    unii_zip: Path = field(init=False)
    unii_names: DelimitedFile = field(init=False)

    def __post_init__(self) -> None:
        drugsfda_zip = self.root / "drugsfda" / "drugsatfda.zip"
        object.__setattr__(self, "unii_records", self.root / "unii" / "UNII_Data.zip")
        object.__setattr__(self, "mesh_supplementary", self.root / "mesh" / "supp2026.xml")
        unii_zip = self.root / "unii" / "UNIIs.zip"
        orangebook = self.root / "orangebook" / "x"
        object.__setattr__(self, "drugsfda_zip", drugsfda_zip)
        object.__setattr__(self, "unii_zip", unii_zip)

        object.__setattr__(
            self, "applications",
            DelimitedFile(drugsfda_zip, TAB, 4, 29_323, "Applications.txt"),
        )
        object.__setattr__(
            self, "products",
            DelimitedFile(drugsfda_zip, TAB, 8, 51_742, "Products.txt"),
        )
        object.__setattr__(
            self, "submissions",
            DelimitedFile(drugsfda_zip, TAB, 8, 193_687, "Submissions.txt"),
        )
        object.__setattr__(
            self, "submission_class_lookup",
            DelimitedFile(drugsfda_zip, TAB, 3, 28, "SubmissionClass_Lookup.txt"),
        )
        object.__setattr__(
            self, "action_types_lookup",
            DelimitedFile(drugsfda_zip, TAB, 4, 59, "ActionTypes_Lookup.txt"),
        )
        object.__setattr__(
            self, "submission_action_types",
            DelimitedFile(
                drugsfda_zip, TAB, 5, 209_312,
                "Join_Submission_ActionTypes_Lookup.txt",
            ),
        )
        object.__setattr__(
            self, "submission_property_type",
            DelimitedFile(drugsfda_zip, TAB, 5, 318_876, "SubmissionPropertyType.txt"),
        )
        object.__setattr__(
            self, "application_docs",
            DelimitedFile(drugsfda_zip, TAB, 8, 81_039, "ApplicationDocs.txt"),
        )
        object.__setattr__(
            self, "ob_products",
            DelimitedFile(orangebook / "products.txt", TILDE, 14, 48_664),
        )
        object.__setattr__(
            self, "ob_patent",
            DelimitedFile(orangebook / "patent.txt", TILDE, 10, 22_205),
        )
        object.__setattr__(
            self, "ob_exclusivity",
            DelimitedFile(orangebook / "exclusivity.txt", TILDE, 5, 2_370),
        )
        object.__setattr__(
            self, "label_shards",
            tuple(
                self.root / "label" / f"drug-label-{n:04d}-of-0014.json.zip"
                for n in range(1, 15)
            ),
        )
        object.__setattr__(
            self, "ndc_json",
            self.root / "ndc" / "drug-ndc-0001-of-0001.json.zip",
        )
        object.__setattr__(
            self, "unii_names",
            DelimitedFile(unii_zip, TAB, 4, 1_050_022, self.unii_snapshot),
        )

    @property
    def exists(self) -> bool:
        """Whether the bulk tree is present. It is gitignored and rebuildable."""
        return self.root.is_dir()


@dataclass(frozen=True)
class BiokdeConnection:
    """How to reach the Insilicom knowledge graph. No client is built here.

    ``pubmedEntitiesFlat`` is the synonym dictionary this project normalises
    against: one row per (name, entity), carrying the entity's ``id`` — a
    ``MESH:``, ``PubChem:`` or other prefixed identifier — its official name
    and its type. Lookups on ``name`` are **case-sensitive** by default, which
    silently loses ``keytruda``; the collection carries a case-insensitive
    index under the collation below, and every name query must pass it.

    Credentials live in a gitignored file rather than here. The file in this
    repo names ``localhost`` but authenticates against the host below, so the
    host is taken from this record and only the user and password from the file.
    """

    host: str = "galaxy3"
    port: int = 27017
    database: str = "biokde"
    auth_source: str = "admin"
    credentials: Path = Path("creds.json")
    #: The collection holding one row per (synonym, entity).
    entities: str = "pubmedEntitiesFlat"
    #: Matches the ``name_1_type_1_ci`` index: strength 2 is case- and
    #: accent-insensitive. A query without it does not use that index.
    collation: tuple[str, int] = ("en", 2)
    #: Seconds before a lookup gives up. The graph is another team's server.
    timeout_ms: int = 8_000

    @property
    def has_credentials(self) -> bool:
        """Whether the credentials file is present. It is gitignored."""
        return self.credentials.is_file()


@dataclass(frozen=True)
class TrialArchive:
    """The local ClinicalTrials.gov archive, used only to validate NCT ids.

    Lives outside this repo — it is the clinical-trials project's output. We
    read **only its manifest**, which is one line per study with the id and
    whether results were posted, so validation costs a 600,762-row scan and
    never touches the 2 GB of study documents.
    """

    root: Path
    manifest_name: str = "manifest.jsonl.gz"
    studies: int = 600_762

    #: The study documents themselves, one JSON object per line per shard.
    #: Read **only** when a start date is needed — the manifest carries the id
    #: and whether results were posted, but no dates, and these shards are 2 GB.
    study_glob: str = "studies-*.jsonl.gz"

    @property
    def manifest(self) -> Path:
        return self.root / self.manifest_name

    @property
    def study_shards(self) -> tuple[Path, ...]:
        """Every study shard, in name order. Empty when the archive is absent."""
        return tuple(sorted(self.root.glob(self.study_glob)))

    @property
    def exists(self) -> bool:
        return self.manifest.is_file()


@dataclass(frozen=True)
class MeshVocabulary:
    """The MeSH descriptor file, used to code label indications to a disease id.

    Lives outside this repo, like :class:`TrialArchive` — it is a MeSH release,
    not FDA bulk data, and it is shared with other projects on this machine.

    **The XML is parsed rather than a neighbouring project's derived JSON.**
    A sibling pipeline keeps a ``mesh_info.json`` of disease synonyms, and
    reusing it would inherit that project's decisions about which branches and
    which term types count as a disease. Those decisions belong to this
    project, so the authoritative release file is read here and the subsetting
    is done in :mod:`source.mesh_descriptors` where it can be tested.
    """

    descriptors: Path
    #: Descriptors in the 2026 release. Checked, not assumed.
    records: int = 31_108

    @property
    def exists(self) -> bool:
        return self.descriptors.is_file()


#: Where the MeSH release sits today. Overridable on the command line for the
#: same reason the trial archive is: it is not part of this repo.
DEFAULT_MESH_DESCRIPTORS = Path(
    "/data/mliu/ontology_normalization_tp/external_data/MeSH/desc2026.xml"
)


#: The structured label extract another team maintains: one row per label,
#: carrying ``active_ingredients`` beside ``moiety_names`` and both their
#: UNIIs. Used only to map a salt to the moiety a trial would name. Current
#: labels only, so it says nothing about what any past submission approved.
DEFAULT_MOIETY_EXTRACT = Path(
    "/space/feng/share/drug_label/drug_indication_market.csv.gz"
)


#: Where the trial archive sits today. Overridable on the command line, because
#: it is another project's output directory and not part of this repo.
DEFAULT_TRIAL_ARCHIVE = Path(
    "/data/mliu/insilicom-clinical_trials/final/output_raw"
)


__all__ = [
    "DEFAULT_MESH_DESCRIPTORS",
    "DEFAULT_MOIETY_EXTRACT",
    "BiokdeConnection",
    "DEFAULT_TRIAL_ARCHIVE",
    "TAB",
    "TILDE",
    "DelimitedFile",
    "MeshVocabulary",
    "RawData",
    "TrialArchive",
]
