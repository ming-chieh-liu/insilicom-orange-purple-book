"""Put both sides of the edge in one id space: MeSH, keyed from exact ids (D42).

The FDA names a drug ``PEMBROLIZUMAB`` in ``Products.txt``; a trial names it
``Keytruda``, ``MK3475`` or ``SCH 900475``. Neither file carries the other's
identifier, so nothing joins until both are normalised.

**FDA side — exact ids only.** Since D42 an ingredient is never resolved by
name. :mod:`service.drug_identity` gives it its UNII, the MeSH record that
lists that UNII (NLM's own ``RegistryNumber``), and the iKraph2 entity whose
``db_id`` is that UNII; :func:`with_identities` turns those into the drug rows.
An ingredient no exact id identifies has no row.

**Trial side — NLM first, then spellings.** ClinicalTrials.gov's own MeSH
coding (D21) is read by P3 directly; this module matches the strings NLM did
not code, through :func:`synonym_index`: every spelling of each FDA-side id —
iKraph2's names of its entity, the FDA's UNII names (brand and code names
included), MeSH entry terms. A spelling two ids claim is dropped.

**Nothing is rolled up here (D44).** ``Gastric Cancer`` is ``MESH:M0333444``
where ``Stomach Neoplasms`` is ``MESH:D013274``; both are kept as stored, and
S8's indication filter places a concept id under its descriptor when it
compares (:func:`service.candidates.build`, ``descriptor_of``).

**Short synonyms are not usable.** ``PB`` and ``PEM`` are spellings of
pembrolizumab; matching an arm called "PB" would be an accident, so synonyms
shorter than :data:`MIN_SYNONYM_LENGTH` are dropped.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from collections.abc import Mapping as ReadOnlyMap
from typing import TYPE_CHECKING, NamedTuple, Protocol

from config.settings import RawData, TrialArchive
from service import approval_events, identity_census
from source import trial_studies
from source.moiety import is_biosimilar
from source.raw_table import read_rows
from utility import application_number as appno
from utility.logging import get_logger

if TYPE_CHECKING:
    from service.drug_identity import DrugIdentity
    from service.registry_identity import Graph, Resolved
    from source.unii_mesh import MeshIndex

_log = get_logger(__name__)

#: Study-design vocabulary that is never the drug under test, however the
#: graph has it indexed. These reach the synonym index because biokde is built
#: by entity recognition over PubMed and the model occasionally tags an
#: ordinary word as a chemical: ``Control`` is a synonym of
#: ``MESH:D002707 Chlordiazepoxide`` with ``cnt=2``, against
#: ``Chlordiazepoxide`` itself at 5,419. One such entry pulls every
#: control-armed trial in the registry onto one drug.
#:
#: Only the **fallback** path needs this. The primary trial-side mapping is
#: ClinicalTrials.gov's own ``derivedSection`` (D21), where NLM does not tag a
#: control arm as a chemical. This guards the 54% of studies that have no
#: derived MeSH and still go through the synonym index.
DESIGN_WORDS: frozenset[str] = frozenset(
    {
        "control", "controls", "placebo", "placebos", "standard", "standards",
        "test", "tests", "normal", "usual care", "standard of care",
        "standard care", "best supportive care", "comparator", "active",
        "active comparator", "vehicle", "sham", "dummy", "blank", "baseline",
        "intervention", "treatment", "therapy", "drug", "study drug",
        "study medication", "medication", "none", "no intervention",
        "observation", "routine care", "conventional", "conventional therapy",
        "saline", "carrier", "urine", "water",
    }
)

#: ``basis`` of a trial string matched only after :func:`dose_stripped`.
BASIS_DOSE_STRIPPED: str = "synonym_dose_stripped"

#: Shortest synonym that may be matched. Below this the graph's abbreviations
#: collide with arm labels, units and initials.
MIN_SYNONYM_LENGTH: int = 4

#: Ids that are not identifiers. The graph writes ``NA`` where an entity has no
#: external id, so 90 FDA ingredients — acoltremon, acoramidis hydrochloride,
#: anacaulase-bcdb, arimoclomol citrate — all "resolved" to the same string.
#: Keyed on that, every unidentified drug would be the same drug, and the
#: candidate generation in P3 would match all of them to each other. Newer
#: drugs are exactly the ones that lack an id, so this is not a rare corner.
PLACEHOLDER_IDS: frozenset[str] = frozenset({"NA", "", "-", "None", "null"})


def is_identifier(mesh_id: str) -> bool:
    """Whether an id identifies anything.

    >>> is_identifier("MESH:D016190")
    True
    >>> is_identifier("PubChem:CID2244")
    True
    >>> is_identifier("NA")
    False
    >>> is_identifier("")
    False
    """
    return mesh_id not in PLACEHOLDER_IDS


class Mapping(NamedTuple):
    """One source string and the entity it normalises to.

    Written out as ``source_name, mesh_id, biokdeid, preferred_name,
    entity_type, basis`` — three identifiers and two names in one row, so the
    columns are named for what they hold rather than generically.
    """

    #: The string as the source wrote it: an FDA ingredient, a trial
    #: intervention, or a MeSH preferred term for an indication.
    name: str
    #: The external vocabulary id — ``MESH:``, ``PubChem:`` and others.
    mesh_id: str
    #: The graph's internal node id — the key ``pubmedRelations`` uses. Carried
    #: so P5 can walk drug → disease edges → supporting PMIDs without resolving
    #: every entity a second time.
    biokdeid: str
    #: The entity's canonical name, for a human reading the table.
    official_name: str
    entity_type: str
    #: ``exact`` when the graph answered directly, ``rolled-up`` when a MeSH
    #: concept id was replaced by the descriptor heading it.
    basis: str


class Coverage(NamedTuple):
    """What a normalisation pass resolved, and what it did not."""

    label: str
    asked: int
    resolved: int
    rolled_up: int
    non_mesh: int
    unresolved: tuple[str, ...]

    @property
    def rate(self) -> float:
        """Share of asked-for names that resolved.

        >>> Coverage("drugs", 10, 8, 0, 0, ()).rate
        0.8
        >>> Coverage("drugs", 0, 0, 0, 0, ()).rate
        0.0
        """
        return self.resolved / self.asked if self.asked else 0.0


def as_graph_id(mesh_id: str) -> str:
    """Prefix a bare MeSH id, because the graph stores them prefixed.

    ``indications.tsv`` writes ``D002137`` — it was built straight from the
    descriptor release, which has no notion of other vocabularies. The graph
    stores ``MESH:D002137``, because it holds PubChem and others alongside. A
    lookup with the bare form matches nothing and reports honest-looking zeros.

    >>> as_graph_id("D002137")
    'MESH:D002137'
    >>> as_graph_id("MESH:D002137")
    'MESH:D002137'
    >>> as_graph_id("PubChem:CID1")
    'PubChem:CID1'
    >>> as_graph_id("")
    ''
    """
    if not mesh_id or ":" in mesh_id:
        return mesh_id
    head = mesh_id[0]
    if head in {"D", "C", "M"} and mesh_id[1:].isdigit():
        return f"MESH:{mesh_id}"
    return mesh_id


#: A dose, a strength or a frequency: a number with its unit ("0.5 mcg/kg",
#: "1%", "100 U/mL", "x 2") or a schedule word. None of them changes which
#: drug is meant, so stripping them cannot change the id a name resolves to.
_DOSE = re.compile(
    r"""
    \b\d+(?:[.,]\d+)?\s*(?:-\s*\d+(?:[.,]\d+)?\s*)?
      (?:mg|mcg|µg|μg|ug|ng|g|kg|ml|l|iu|miu|u|units?|mmol|µmol|umol|mol|meq|%|
         mg/kg|mcg/kg|µg/kg|ug/kg|mg/m2|mg/m²|mg/ml|mcg/ml|u/ml|iu/ml|mg/day|mg/d)
      (?:\s*/\s*(?:kg|m2|m²|ml|day|d|dose|week|wk|h|hr))?
    (?![a-z])
    | \b\d+(?:[.,]\d+)?\s*%
    | \bx\s*\d+\b
    | \b(?:once|twice|three\s+times|four\s+times)\s+(?:a\s+)?(?:daily|weekly|monthly|day|week)\b
    | \b(?:every|q)\s*\d+\s*(?:hours?|h|days?|d|weeks?|w|months?)\b
    | \b(?:qd|bid|tid|qid|qw|q2w|q3w|q4w|qod|prn|daily|weekly|monthly)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: Routes and dosage forms. Each names how a drug is given, never which drug.
_FORM_WORDS: frozenset[str] = frozenset({
    "oral", "orally", "po", "intravenous", "intravenously", "iv", "subcutaneous",
    "subcutaneously", "sc", "sq", "intramuscular", "im", "topical", "topically",
    "inhaled", "inhalation", "intranasal", "nasal", "ophthalmic", "transdermal",
    "sublingual", "intravitreal", "intrathecal", "vaginal", "rectal",
    "tablet", "tablets", "tab", "tabs", "capsule", "capsules", "cap", "caps",
    "injection", "injections", "injectable", "infusion", "ointment", "cream",
    "gel", "solution", "suspension", "patch", "patches", "spray", "film",
    "foam", "lotion", "drops", "inhaler", "powder", "sachet", "syrup", "elixir",
    "emulsion", "implant", "pen", "vial", "prefilled", "syringe", "formulation",
    "extended-release", "extended", "release", "delayed-release", "modified-release",
    "er", "xr", "sr", "xl", "cr", "dr", "odt", "ir", "dose", "doses", "dosing",
    "continuous", "bolus",
    "mg", "mcg",
})

#: Joining words left dangling once a dose is removed ("10 mg in 2 mL").
_DANGLING: frozenset[str] = frozenset({"in", "of", "per", "and", "or", "with", "at", "by", "for"})


def dose_stripped(name: str) -> str:
    """``name`` without its dose, schedule, route or dosage form; "" if unchanged.

    Only removals, never rewrites: what is left must be a run of the original
    words, so a lookup on it can only find a synonym the string already
    contained. A result that is a study-design word, or shorter than
    :data:`MIN_SYNONYM_LENGTH`, is refused.

    >>> dose_stripped("CR845 0.5 mcg/kg")
    'CR845'
    >>> dose_stripped("KX2-391 Ointment 1%")
    'KX2-391'
    >>> dose_stripped("Lecanemab 10 mg/kg IV every 2 weeks")
    'Lecanemab'
    >>> dose_stripped("Insulin glargine 100 U/mL")
    'Insulin glargine'
    >>> dose_stripped("Zanubrutinib")
    ''
    >>> dose_stripped("Placebo tablets")
    ''
    >>> dose_stripped("Continuous Dose")
    ''
    >>> dose_stripped("Drug A 10 mg + Drug B 5 mg")
    'Drug A + Drug B'
    """
    text = _DOSE.sub(" ", name)
    words = [w for w in re.split(r"\s+", text) if w]
    kept = [w for w in words if w.strip(",;:()[]").casefold() not in _FORM_WORDS]
    while kept and kept[-1].strip(",;:()[]-").casefold() in _DANGLING | {""}:
        kept.pop()
    while kept and kept[0].strip(",;:()[]-").casefold() in _DANGLING | {""}:
        kept.pop(0)
    result = " ".join(kept).strip(" ,;:-/")
    if result.endswith("(") or result.count("(") != result.count(")"):
        result = result.replace("(", " ").replace(")", " ")
        result = " ".join(result.split())
    if (not result or result.casefold() == " ".join(name.split()).casefold()
            or len(result) < MIN_SYNONYM_LENGTH or result.casefold() in DESIGN_WORDS):
        return ""
    return result


#: Shortest name a dose-stripped string may be looked up by. Longer than
#: :data:`MIN_SYNONYM_LENGTH`: in a 200-row check every short leftover that
#: resolved ("NaCl", "MSCs", "site", "Salt") resolved to the wrong drug.
MIN_STRIPPED_LENGTH: int = 5


def nlm_agrees(mesh_id: str, nlm_mesh: set[str] | None) -> bool:
    """Whether NLM's coding of the same trials leaves ``mesh_id`` standing.

    True when there is nothing to contradict it — a non-MeSH id, or no trial
    naming the string has NLM coding — and otherwise only if NLM assigned it.

    >>> nlm_agrees("MESH:D005902", {"MESH:D008103"}), nlm_agrees("MESH:D008103", {"MESH:D008103"})
    (False, True)
    >>> nlm_agrees("MESH:D005902", None), nlm_agrees("PubChem:CID1", {"MESH:D1"})
    (True, True)
    """
    if mesh_id.startswith("MESH:") and nlm_mesh:
        return mesh_id in nlm_mesh
    return True


class NormalizeReport(NamedTuple):
    """The four mappings, and what each pass covered."""

    drugs: tuple[Mapping, ...]
    indications: tuple[Mapping, ...]
    #: D44: trial strings and NLM-coded ids with the exact ids they reached.
    trial_drugs: tuple[Resolved, ...]
    trial_conditions: tuple[Resolved, ...]
    nlm_ids: tuple[Resolved, ...]
    coverages: tuple[Coverage, ...]
    #: Trial-relevant applications whose ingredients all failed to resolve.
    applications_unresolved: int
    applications_total: int

    @property
    def application_rate(self) -> float:
        """Share of trial-relevant applications with at least one drug id."""
        if not self.applications_total:
            return 0.0
        resolved = self.applications_total - self.applications_unresolved
        return resolved / self.applications_total


def ingredients_of(
    data: RawData, applications: frozenset[str]
) -> dict[str, frozenset[str]]:
    """Application → its distinct active ingredient names.

    A combination product lists its ingredients separated by ``;`` in one
    field, so one product can name several drugs. Both are kept: KEYNOTE-189
    approved pembrolizumab *with* chemotherapy, and a mapping that dropped the
    partners could not represent that.
    """
    # Products.txt carries no ApplType, so its rows cannot be keyed on their
    # own — only Applications.txt says whether 000001 is an NDA or an ANDA.
    canonical_of: dict[str, str] = {}
    for row in read_rows(data.applications):
        parsed = appno.from_parts(row.get("ApplType"), row.get("ApplNo"))
        if parsed.is_application:
            canonical_of[parsed.digits] = parsed.canonical

    out: dict[str, set[str]] = {}
    for row in read_rows(data.products):
        application = canonical_of.get(appno.pad(row.get("ApplNo")), "")
        if not application or application not in applications:
            continue
        for part in (row.get("ActiveIngredient") or "").split(
            identity_census.INGREDIENT_SEPARATOR
        ):
            name = part.strip()
            if name:
                out.setdefault(application, set()).add(name)
    return {key: frozenset(value) for key, value in out.items()}


#: ``basis`` of a drug row whose MeSH id came from its UNII (D42).
BASIS_UNII_REGISTRY = "unii_registry"
#: ``basis`` of a suffixed biologic whose MeSH record is the molecule without
#: the suffix: for a biosimilar that names a **different**, earlier product, so
#: the id is a retrieval key, never an identity claim (D11). P3 orders such a
#: pool with biosimilar-looking studies first.
BASIS_STEM = "stem"


def with_identities(identities: dict[str, DrugIdentity]) -> dict[str, Mapping]:
    """The FDA drug rows under D42, from exact ids only — no name resolution.

    * an ingredient whose UNII maps to MeSH takes that id (``unii_registry``,
      or ``stem`` for a suffixed biologic filed under the bare molecule);
    * one with no MeSH record but an iKraph2 node is kept under its UNII, so
      the literature route can walk it and the trial side can match its names;
    * one with neither has no row: nothing exact identifies it;
    * ``biokdeid`` is the iKraph2 node(s), ``;``-joined, or "".

    >>> from service.drug_identity import DrugIdentity
    >>> ids = {"A": DrugIdentity("A", "U1", "MESH:D9", "Nine", "27", "TherapyPreventions",
    ...                          "unii_registry"),
    ...        "B": DrugIdentity("B", "U2", "", "", "28", "TherapyPreventions", "unii_no_mesh"),
    ...        "ADALIMUMAB-AACF": DrugIdentity("ADALIMUMAB-AACF", "U3", "MESH:D1", "Adalimumab",
    ...                                        "", "", "unii_registry"),
    ...        "C": DrugIdentity("C", "U4", "", "", "", "", "unii_no_mesh")}
    >>> [(m.name, m.mesh_id, m.biokdeid, m.basis) for m in with_identities(ids).values()]
    ... # doctest: +NORMALIZE_WHITESPACE
    [('A', 'MESH:D9', '27', 'unii_registry'), ('B', 'UNII:U2', '28', 'unii_no_mesh'),
     ('ADALIMUMAB-AACF', 'MESH:D1', '', 'stem')]
    """
    out: dict[str, Mapping] = {}
    for name, identity in identities.items():
        if identity.mesh_id:
            stem = is_biosimilar(name) and name.casefold() != identity.mesh_name.casefold()
            out[name] = Mapping(name, identity.mesh_id, identity.nodes, identity.mesh_name,
                                identity.node_type or "Chemical",
                                BASIS_STEM if stem else BASIS_UNII_REGISTRY)
        elif identity.nodes:
            first = identity.uniis.split(";")[0]
            out[name] = Mapping(name, f"UNII:{first}", identity.nodes, "",
                                identity.node_type, identity.basis)
    return out


def fda_rows(names: Iterable[str], identities: dict[str, DrugIdentity],
             by_name: dict[str, Resolved]) -> dict[str, Mapping]:
    """The FDA drug rows (D44): exact ids from the UNII (:func:`with_identities`),
    else an exact name match (``by_name``); an ingredient neither reaches has no row.

    >>> from service.drug_identity import DrugIdentity
    >>> from service.registry_identity import Resolved
    >>> ids = {"A": DrugIdentity("A", "U1", "MESH:D9", "Nine", "27", "TherapyPreventions",
    ...                          "unii_registry")}
    >>> by_name = {"B": Resolved("B", "28", "", "MESH:C5", "", "drug", "ikraph2_name"),
    ...            "C": Resolved("C", "29", "U3", "", "", "drug", "unii_name")}
    >>> [(m.name, m.mesh_id, m.biokdeid, m.basis) for m in fda_rows("ABC", ids, by_name).values()]
    ... # doctest: +NORMALIZE_WHITESPACE
    [('A', 'MESH:D9', '27', 'unii_registry'), ('B', 'MESH:C5', '28', 'ikraph2_name'),
     ('C', 'UNII:U3', '29', 'unii_name')]
    """
    out = with_identities({n: identities[n] for n in names if n in identities})
    for name in names:
        row = by_name.get(name)
        if name in out or row is None:
            continue
        first = (row.mesh_id or row.pubchem).split(";")[0] or (
            f"UNII:{row.unii.split(';')[0]}" if row.unii else "")
        if first or row.biokdeid:
            out[name] = Mapping(name, first, row.biokdeid, "", row.entity_type, row.basis)
    return out


def build(
    data: RawData,
    archive: TrialArchive,
    graph: Graph,
    mesh: MeshIndex,
    indication_ids: list[str],
    identities: dict[str, DrugIdentity],
    unii_by_name: dict[str, frozenset[str]],
    descriptor_names: dict[str, str] | None = None,
) -> NormalizeReport:
    """S2's FDA side and S3's registry side, both by exact ids only (D44).

    FDA ingredients take their UNII-derived ids (D42), else an exact name
    match. Every registry intervention and condition string is matched by
    exact name, and every NLM-coded MeSH id by exact id, to iKraph2 entities
    (:mod:`service.registry_identity`). Indications get their one Disease
    entity by exact id. Nothing is rolled up: S8 places ids in the MeSH tree.
    """
    from service import registry_identity

    spine = approval_events.build(data)
    applications = frozenset(
        row.application for row in spine.rows if row.trial_relevant == "true"
    )
    by_application = ingredients_of(data, applications)
    names = sorted({name for names in by_application.values() for name in names})
    missing = [n for n in names if not (identities.get(n) and (
        identities[n].mesh_id or identities[n].nodes))]
    drugs = fda_rows(names, identities, registry_identity.drug_strings(
        missing, mesh, unii_by_name, graph))
    drug_coverage = Coverage(
        label="FDA drug (active ingredient, exact ids)",
        asked=len(names),
        resolved=len(drugs),
        rolled_up=0,
        non_mesh=sum(1 for m in drugs.values() if not m.mesh_id.startswith("MESH:")),
        unresolved=tuple(n for n in names if n not in drugs)[:50],
    )
    unresolved_applications = sum(
        1
        for application in applications
        if not any(name in drugs for name in by_application.get(application, ()))
    )

    titles = descriptor_names or {}
    indication_meshes = list(dict.fromkeys(as_graph_id(i) for i in indication_ids if i))
    indication_internal = graph.internal_ids(indication_meshes)
    indications = tuple(sorted(
        Mapping(
            name=titles.get(mesh_id.removeprefix("MESH:"), "") or mesh_id,
            mesh_id=mesh_id,
            biokdeid=indication_internal.get(mesh_id, ""),
            official_name=titles.get(mesh_id.removeprefix("MESH:"), ""),
            entity_type="Disease",
            basis="exact",
        )
        for mesh_id in indication_meshes
    ))
    indication_coverage = Coverage(
        label="FDA indication (label section 1)",
        asked=len(indications), resolved=len(indications),
        rolled_up=0, non_mesh=0, unresolved=(),
    )

    # One pass over the archive feeds both trial-side passes.
    interventions: dict[str, None] = {}
    conditions: dict[str, None] = {}
    nlm_drugs: set[str] = set()
    nlm_conditions: set[str] = set()
    #: Intervention string → the NLM intervention MeSH of every trial naming it
    #: that has any, for the dose-stripped guard (2026-09-30).
    nlm_of: dict[str, set[str]] = {}
    for facts in trial_studies.read_study_facts(archive):
        interventions.update(dict.fromkeys(facts.interventions))
        conditions.update(dict.fromkeys(facts.conditions))
        nlm_drugs.update(facts.derived_drug_mesh)
        nlm_conditions.update(facts.derived_condition_mesh)
        if facts.derived_drug_mesh:
            for name in facts.interventions:
                nlm_of.setdefault(name, set()).update(facts.derived_drug_mesh)
    trial_drugs = registry_identity.drug_strings(interventions, mesh, unii_by_name, graph,
                                                 nlm_of)
    trial_conditions = registry_identity.condition_strings(conditions, mesh, graph)
    nlm_ids = {**registry_identity.drug_mesh(nlm_drugs, mesh, graph),
               **registry_identity.disease_mesh(nlm_conditions, graph)}

    def coverage(label: str, asked: int, rows: Iterable[Resolved]) -> Coverage:
        listed = list(rows)
        return Coverage(label=label, asked=asked, resolved=len(listed), rolled_up=0,
                        non_mesh=sum(1 for r in listed if not r.mesh_id), unresolved=())

    return NormalizeReport(
        drugs=tuple(sorted(drugs.values())),
        indications=indications,
        trial_drugs=tuple(sorted(trial_drugs.values())),
        trial_conditions=tuple(sorted(trial_conditions.values())),
        nlm_ids=tuple(sorted(nlm_ids.values())),
        coverages=(
            drug_coverage,
            indication_coverage,
            coverage("trial intervention", len(interventions), trial_drugs.values()),
            coverage("trial condition", len(conditions), trial_conditions.values()),
            coverage("NLM-coded MeSH id", len(nlm_ids),
                     (r for r in nlm_ids.values() if r.biokdeid)),
        ),
        applications_unresolved=unresolved_applications,
        applications_total=len(applications),
    )


def render(report: NormalizeReport) -> str:
    """The pass report, as ``--mode normalize`` prints it."""
    return "\n".join(
        [
            render_coverage(report.coverages),
            "  trial-relevant applications",
            f"    total                                  "
            f"{report.applications_total:>9,}",
            f"    with at least one drug id              "
            f"{report.applications_total - report.applications_unresolved:>9,}"
            f"  ({100 * report.application_rate:.1f}%)",
        ]
    )


def render_coverage(coverages: tuple[Coverage, ...]) -> str:
    """The per-mapping coverage report."""
    lines = ["Normalisation coverage", ""]
    for coverage in coverages:
        lines += [
            f"  {coverage.label}",
            f"    asked                                  {coverage.asked:>9,}",
            f"    resolved                               {coverage.resolved:>9,}"
            f"  ({100 * coverage.rate:.1f}%)",
            f"    rolled up from a MeSH concept id       {coverage.rolled_up:>9,}",
            f"    kept under a non-MeSH vocabulary       {coverage.non_mesh:>9,}",
        ]
        if coverage.unresolved:
            shown = ", ".join(coverage.unresolved[:8])
            lines.append(f"    unresolved (first few): {shown}")
        lines.append("")
    return "\n".join(lines)


def counted(mappings: dict[str, Mapping]) -> tuple[tuple[str, int], ...]:
    """The most-claimed ids, for eyeballing a pass that went wrong."""
    counter: Counter[str] = Counter(m.mesh_id for m in mappings.values())
    return tuple(counter.most_common(5))


__all__ = [
    "BASIS_DOSE_STRIPPED",
    "BASIS_STEM",
    "BASIS_UNII_REGISTRY",
    "DESIGN_WORDS",
    "MIN_STRIPPED_LENGTH",
    "MIN_SYNONYM_LENGTH",
    "PLACEHOLDER_IDS",
    "Coverage",
    "Mapping",
    "NormalizeReport",
    "as_graph_id",
    "build",
    "counted",
    "dose_stripped",
    "fda_rows",
    "ingredients_of",
    "is_identifier",
    "nlm_agrees",
    "render",
    "render_coverage",
    "with_identities",
]
