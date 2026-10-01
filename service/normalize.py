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

**Concept ids and descriptor ids are different ids for the same thing.**
``Gastric Cancer`` is ``MESH:M0333444`` where ``Stomach Neoplasms`` is
``MESH:D013274``; :func:`roll_up` replaces a concept id with its descriptor.

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


def roll_up(mesh_id: str, concepts: dict[str, str]) -> str:
    """Replace a MeSH concept id with the descriptor that heads it.

    Non-MeSH ids and ids with no descriptor pass through untouched: a
    PubChem-only drug still needs a key.

    >>> roll_up("MESH:M0333444", {"M0333444": "D013274"})
    'MESH:D013274'
    >>> roll_up("MESH:D013274", {"D013274": "D013274"})
    'MESH:D013274'
    >>> roll_up("PubChem:CID1", {})
    'PubChem:CID1'
    >>> roll_up("MESH:C582435", {})
    'MESH:C582435'
    """
    if not mesh_id.startswith("MESH:"):
        return mesh_id
    bare = mesh_id.removeprefix("MESH:")
    descriptor = concepts.get(bare)
    return f"MESH:{descriptor}" if descriptor else mesh_id


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


def stripped_match_ok(stripped: str, mesh_id: str, nlm_mesh: set[str] | None) -> bool:
    """Whether a dose-stripped name may keep the id it resolved to.

    Two guards, both from the 200-row precision check of 2026-09-30. A short
    leftover is refused. And where NLM coded the interventions of the trials
    naming this string, a MeSH id NLM never assigned is refused: every wrong
    match in the check ("surgery" → Sulfur, "Abatacept" → Belatacept) sat on
    trials NLM had coded correctly and differently. A non-MeSH id, or a string
    no trial has NLM coding for, has nothing to be contradicted by.

    >>> stripped_match_ok("CR845", "PubChem:CID24794466", {"MESH:D000700"})
    True
    >>> stripped_match_ok("NaCl", "MESH:D000111", None)
    False
    >>> stripped_match_ok("surgery", "MESH:D013455", {"MESH:D013514"})
    False
    >>> stripped_match_ok("Lecanemab", "MESH:C000710711", {"MESH:C000710711"})
    True
    """
    if len(stripped) < MIN_STRIPPED_LENGTH:
        return False
    return nlm_agrees(mesh_id, nlm_mesh)


def synonym_index(
    expanded: ReadOnlyMap[str, Iterable[str]], concepts: dict[str, str]
) -> dict[str, str]:
    """Case-folded synonym → id, for matching the trial side.

    ``expanded`` is each FDA-side id and every spelling known for it (D42:
    iKraph2's names of its entity, FDA's UNII names, MeSH entry terms). The
    cheaper direction: rather than normalising half a million trial strings,
    expand the few thousand ids the FDA side uses and match against that.

    A synonym claimed by more than one id is dropped, not arbitrated — an
    ambiguous name is not evidence, and keeping the first one seen would make
    the result depend on query order.

    >>> synonym_index({"MESH:C1": ["Keytruda", "MK-3475", "PB", "placebo"],
    ...                "MESH:C2": ["Opdivo", "MK-3475"]}, {})
    {'keytruda': 'MESH:C1', 'opdivo': 'MESH:C2'}
    """
    claims: dict[str, set[str]] = {}
    dropped_design = 0
    for mesh_id, names in expanded.items():
        if not is_identifier(mesh_id):
            continue
        rolled = roll_up(mesh_id, concepts)
        for name in names:
            folded = name.casefold().strip()
            if folded in DESIGN_WORDS:
                dropped_design += 1
                continue
            if len(folded) >= MIN_SYNONYM_LENGTH:
                claims.setdefault(folded, set()).add(rolled)
    index = {name: next(iter(ids)) for name, ids in claims.items() if len(ids) == 1}
    _log.info(
        "%d synonyms over %d ids (%d ambiguous, %d study-design words dropped)",
        len(index), len(expanded), len(claims) - len(index), dropped_design,
    )
    return index


class NormalizeReport(NamedTuple):
    """The four mappings, and what each pass covered."""

    drugs: tuple[Mapping, ...]
    indications: tuple[Mapping, ...]
    trial_drugs: tuple[Mapping, ...]
    trial_conditions: tuple[Mapping, ...]
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


def _match_trial_strings(
    values: Iterable[tuple[str, ...]],
    index: dict[str, str],
    label: str,
    biokdeids: dict[str, str] | None = None,
    names: dict[str, str] | None = None,
    nlm_of: dict[str, set[str]] | None = None,
    strip_doses: bool = False,
) -> tuple[tuple[Mapping, ...], Coverage]:
    """Map distinct trial strings through a synonym index.

    The cheap direction. Normalising all 493,446 distinct intervention strings
    against the graph would be a round trip each; expanding the few thousand
    ids the FDA side actually uses into every spelling the graph knows and
    matching locally is one pass over the archive.

    ``strip_doses`` (interventions only) retries an unmatched string without
    its dose, route and form — never for conditions, where "Oral Mucositis" or
    "Nasal Polyps" lose their meaning without the first word.
    """
    distinct: dict[str, None] = {}
    for group in values:
        for value in group:
            if value:
                distinct.setdefault(value, None)
    rows: list[Mapping] = []
    for value in distinct:
        mesh_id = index.get(value.casefold())
        basis = "synonym"
        if not mesh_id and strip_doses:
            # The same name with its dose, route or form removed ("CR845 0.5
            # mcg/kg" -> "CR845"); none of those changes which drug it is.
            stripped = dose_stripped(value)
            mesh_id = index.get(stripped.casefold()) if stripped else None
            basis = BASIS_DOSE_STRIPPED
            if mesh_id and not stripped_match_ok(stripped, mesh_id, (nlm_of or {}).get(value)):
                mesh_id = None
        if mesh_id:
            rows.append(
                Mapping(
                    name=value,
                    mesh_id=mesh_id,
                    biokdeid=(biokdeids or {}).get(mesh_id, ""),
                    # Inherited from the FDA-side row that resolved this id: a
                    # synonym-index row never went through a name lookup, so
                    # without this the column is blank on every trial row.
                    official_name=(names or {}).get(mesh_id, ""),
                    entity_type="",
                    basis=basis,
                )
            )
    coverage = Coverage(
        label=label,
        asked=len(distinct),
        resolved=len(rows),
        rolled_up=0,
        non_mesh=sum(1 for row in rows if not row.mesh_id.startswith("MESH:")),
        unresolved=(),
    )
    _log.info("%s: %d of %d distinct strings matched", label, len(rows), len(distinct))
    return tuple(sorted(rows)), coverage


#: ``basis`` of a drug row whose MeSH id came from its UNII (D42).
BASIS_UNII_REGISTRY = "unii_registry"
#: ``basis`` of a suffixed biologic whose MeSH record is the molecule without
#: the suffix: for a biosimilar that names a **different**, earlier product, so
#: the id is a retrieval key, never an identity claim (D11). P3 orders such a
#: pool with biosimilar-looking studies first.
BASIS_STEM = "stem"


class GraphNames(Protocol):
    """What S2/S3 read from the graph (D42: :class:`dao.ikraph2_entities.Ikraph2Entities`)."""

    def names_of(self, biokdeids: Iterable[str]) -> dict[str, frozenset[str]]: ...

    def internal_ids(self, ids: Iterable[str]) -> dict[str, str]: ...


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


def expand(rows: Iterable[Mapping], graph: GraphNames,
           extra: dict[str, set[str]]) -> dict[str, set[str]]:
    """Each row's id → every spelling known for it (D42).

    The graph's names of the row's iKraph2 node(s), plus ``extra`` keyed by
    the row's source name or by its id (FDA UNII names, MeSH entry terms), plus
    the source name itself.

    >>> class G:
    ...     def names_of(self, ids): return {"27": frozenset({"lambrolizumab"})}
    ...     def internal_ids(self, ids): return {}
    >>> rows = [Mapping("PEMBROLIZUMAB", "MESH:C1", "27", "", "", "")]
    >>> sorted(expand(rows, G(), {"PEMBROLIZUMAB": {"Keytruda"}, "MESH:C1": {"MK-3475"}})["MESH:C1"])
    ['Keytruda', 'MK-3475', 'PEMBROLIZUMAB', 'lambrolizumab']
    """
    rows = list(rows)
    names_of = graph.names_of({n for m in rows for n in m.biokdeid.split(";") if n})
    out: dict[str, set[str]] = {}
    for m in rows:
        if not is_identifier(m.mesh_id):
            continue
        names = out.setdefault(m.mesh_id, set())
        names.add(m.name)
        names |= extra.get(m.name, set()) | extra.get(m.mesh_id, set())
        for node in m.biokdeid.split(";"):
            names |= names_of.get(node, frozenset())
    return out


def build(
    data: RawData,
    archive: TrialArchive,
    graph: GraphNames,
    concepts: dict[str, str],
    indication_ids: list[str],
    identities: dict[str, DrugIdentity],
    descriptor_names: dict[str, str] | None = None,
    extra_names: dict[str, set[str]] | None = None,
) -> NormalizeReport:
    """Run all four normalisation passes and report what each covered (D42).

    The FDA side comes from ``identities`` (:mod:`service.drug_identity`):
    exact ids, no name lookup. Indications get their iKraph2 internal id by
    exact ``db_id``. The trial side is matched on every spelling of each id —
    iKraph2's names, plus ``extra_names`` (an ingredient's FDA UNII names, keyed
    by ingredient; MeSH entry terms, keyed by MeSH id).
    """
    spine = approval_events.build(data)
    applications = frozenset(
        row.application for row in spine.rows if row.trial_relevant == "true"
    )
    by_application = ingredients_of(data, applications)
    names = sorted({name for names in by_application.values() for name in names})
    drugs = with_identities({n: identities[n] for n in names if n in identities})
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

    indication_internal = graph.internal_ids(
        [roll_up(as_graph_id(i), concepts) for i in indication_ids]
    )
    # An indication arrives as an id, never as a name, so without the MeSH
    # preferred term both the name and official_name columns would hold the id
    # back again and the table would be unreadable by a human.
    titles = descriptor_names or {}

    def _title(mesh_id: str) -> str:
        return titles.get(mesh_id.removeprefix("MESH:"), "")

    rolled_indications = {
        mesh_id: Mapping(
            name=_title(roll_up(mesh_id, concepts)) or mesh_id,
            mesh_id=roll_up(mesh_id, concepts),
            biokdeid=indication_internal.get(roll_up(mesh_id, concepts), ""),
            official_name=_title(roll_up(mesh_id, concepts)),
            entity_type="Disease",
            basis="rolled-up" if roll_up(mesh_id, concepts) != mesh_id else "exact",
        )
        for mesh_id in dict.fromkeys(as_graph_id(i) for i in indication_ids)
        if mesh_id
    }
    indication_coverage = Coverage(
        label="FDA indication (label section 1)",
        asked=len(rolled_indications),
        resolved=len(rolled_indications),
        rolled_up=sum(
            1 for m in rolled_indications.values() if m.basis == "rolled-up"
        ),
        non_mesh=0,
        unresolved=(),
    )

    extra = extra_names or {}
    drug_index = synonym_index(expand(drugs.values(), graph, extra), concepts)
    condition_index = synonym_index(
        expand(rolled_indications.values(), graph, extra), concepts)
    # One pass over the archive feeds both trial-side passes.
    interventions: list[tuple[str, ...]] = []
    #: Intervention string → the NLM intervention MeSH of every trial naming it
    #: that has any, for the dose-stripped guard (2026-09-30).
    nlm_of: dict[str, set[str]] = {}

    conditions: list[tuple[str, ...]] = []
    for facts in trial_studies.read_study_facts(archive):
        interventions.append(facts.interventions)
        if facts.derived_drug_mesh:
            for name in facts.interventions:
                nlm_of.setdefault(name, set()).update(facts.derived_drug_mesh)
        conditions.append(facts.conditions)
    # The graph's internal id, keyed by the external one, so a trial-side row
    # inherits the biokdeid its FDA-side counterpart already resolved.
    drug_biokdeids = {m.mesh_id: m.biokdeid for m in drugs.values() if m.biokdeid}
    drug_names = {
        m.mesh_id: m.official_name for m in drugs.values() if m.official_name
    }
    trial_drugs, trial_drug_coverage = _match_trial_strings(
        interventions, drug_index, "trial intervention", drug_biokdeids, drug_names,
        nlm_of, strip_doses=True
    )
    condition_biokdeids = {
        m.mesh_id: m.biokdeid for m in rolled_indications.values() if m.biokdeid
    }
    condition_names = {
        m.mesh_id: m.official_name
        for m in rolled_indications.values()
        if m.official_name
    }
    trial_conditions, trial_condition_coverage = _match_trial_strings(
        conditions,
        condition_index,
        "trial condition",
        condition_biokdeids,
        condition_names,
    )
    return NormalizeReport(
        drugs=tuple(sorted(drugs.values())),
        indications=tuple(sorted(rolled_indications.values())),
        trial_drugs=trial_drugs,
        trial_conditions=trial_conditions,
        coverages=(
            drug_coverage,
            indication_coverage,
            trial_drug_coverage,
            trial_condition_coverage,
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
    "MIN_SYNONYM_LENGTH",
    "PLACEHOLDER_IDS",
    "Coverage",
    "GraphNames",
    "Mapping",
    "NormalizeReport",
    "as_graph_id",
    "build",
    "counted",
    "dose_stripped",
    "expand",
    "ingredients_of",
    "is_identifier",
    "render",
    "render_coverage",
    "roll_up",
    "synonym_index",
    "with_identities",
]
