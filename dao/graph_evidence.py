"""Literature evidence for a drug-disease pair, from the Insilicom graph.

Where a label yields nothing — no section 14, or a section that names no trial
— the graph is the only remaining source. It does not know what supported an
approval; it knows what the literature associates. So this reads edges and
returns the registry ids their supporting papers mention, and a judge decides
whether any of them is evidence.

**The chain, and where each piece lives.** ``pubmedRelations`` holds one
document per drug-disease edge, keyed on the *internal* ``biokdeid`` that P1
stores on every mapping row. Its ``locations`` field is where the PMIDs are —
formatted ``"<pmid>.<node_two>.<node_one>"`` — not ``context``, which holds
co-occurring entities, and not ``contList``, which holds their ids.
``pubmedDocs`` then has the abstract.

**NCT ids must be mined from prose.** PubMed records a trial registration as
structured metadata in ``DataBankList``, and this graph's ``pubmedDocs`` does
not carry that field. So recall is bounded by whether an abstract happens to
print the id in its text — measured on 150 pre-2008 submissions, 16% of them
yielded at least one, with a median of 2 distinct ids where they did.

Read-only by construction: no write path.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, NamedTuple

from config.settings import BiokdeConnection
from utility.logging import get_logger

_log = get_logger(__name__)

#: A registry id as an abstract prints it. Tighter than the label pattern:
#: abstract prose does not use the spaced forms a label does.
NCT = re.compile(r"NCT\d{8}")

#: ``locations`` entries look like ``"40419140.55633.6308113"``; the PMID is
#: the first segment.
PMID_IN_LOCATION = re.compile(r"^(\d+)\.")

#: How many abstracts to read for one edge. The median edge cites 461 PMIDs and
#: the largest 6,187, so this bounds a pass that would otherwise be dominated by
#: a handful of mega-drugs. Ordered by nothing in particular — the graph does
#: not rank its evidence — so this is a budget, not a ranking.
ABSTRACT_BUDGET: int = 400

#: PMIDs per ``pubmedDocs`` query.
LOOKUP_BATCH: int = 500


#: ``pubmedRelations.relationship_type`` for **negative correlation** — the
#: drug lowers, treats or cures the disease (D31). The graph stores the type as
#: a bare code; read off pembrolizumab's 2,888 disease edges: ``3`` carries the
#: treatment literature (NSCLC, melanoma, urothelial carcinoma), ``2`` is
#: positive correlation (immune-related adverse events, pneumonitis), ``1`` is
#: association. **``correlation_type`` is a different field with different
#: codes** (its ``0`` coincides with this ``3``); reading it instead is the
#: mistake this constant exists to prevent.
NEGATIVE_CORRELATION: str = "3"

#: ``direction`` codes: ``12`` points node one → node two. An edge whose
#: direction is unrecorded counts as undirected and is kept.
FORWARD: str = "12"
BACKWARD: str = "21"
UNDIRECTED: frozenset[str] = frozenset({"", "0", "NA"})

#: The node type a treatment edge must end on.
DISEASE_TYPE: str = "Disease"


class TreatmentEdge(NamedTuple):
    """One drug → disease negative-correlation edge and the PMIDs behind it."""

    disease_biokdeid: str
    disease_name: str
    score: float
    pmids: tuple[str, ...]


def points_from(drug_is_node_one: bool, direction: str) -> bool:
    """Whether an edge runs from the drug to the disease, or has no direction.

    >>> points_from(True, "12"), points_from(False, "21"), points_from(True, "NA")
    (True, True, True)
    >>> points_from(True, "21"), points_from(False, "12")
    (False, False)
    """
    if direction in UNDIRECTED:
        return True
    return direction == (FORWARD if drug_is_node_one else BACKWARD)


def _pmids(edge: dict[str, Any]) -> tuple[str, ...]:
    found: dict[str, None] = {}
    for location in edge.get("locations") or []:
        if not location:
            continue
        match = PMID_IN_LOCATION.match(str(location[0]))
        if match:
            found[match.group(1)] = None
    return tuple(found)


class Mention(NamedTuple):
    """One registry id an abstract mentioned, and where."""

    nct_id: str
    pmid: str
    title: str
    publication_types: tuple[str, ...]

    @property
    def is_trial_report(self) -> bool:
        """Whether the paper is typed as a trial report.

        PubMed's types are lower-cased in this graph, which a case-sensitive
        check silently misses — it reported zero trial reports the first time.

        >>> Mention("NCT1", "1", "t", ("randomized controlled trial",)).is_trial_report
        True
        >>> Mention("NCT1", "1", "t", ("review",)).is_trial_report
        False
        """
        return any(
            "trial" in kind.lower() or "random" in kind.lower()
            for kind in self.publication_types
        )


class GraphEvidenceRepository:
    """Edge and abstract lookups against one injected, pooled client."""

    def __init__(self, client: Any, connection: BiokdeConnection) -> None:
        database = client[connection.database]
        self._relations = database["pubmedRelations"]
        self._documents = database["pubmedDocs"]

    def pmids_for(self, drug: str, disease: str) -> list[str]:
        """Every PMID supporting the edge between these two internal ids.

        Both orientations are asked for: the graph does not guarantee which
        node is stored first.
        """
        found: set[str] = set()
        query = {
            "$or": [
                {"node_one_id": drug, "node_two_id": disease},
                {"node_one_id": disease, "node_two_id": drug},
            ]
        }
        for edge in self._relations.find(query):
            found.update(_pmids(edge))
        return sorted(found)

    def treatment_edges(self, drug: str) -> list[TreatmentEdge]:
        """Every disease the literature says this drug negatively correlates
        with, best-scored first (D31).

        For approvals with **no indication**: instead of one drug-disease edge,
        every edge of type :data:`NEGATIVE_CORRELATION` from the drug to a
        ``Disease`` node, in either storage orientation, whose direction runs
        drug → disease or is unrecorded.
        """
        query = {
            "relationship_type": NEGATIVE_CORRELATION,
            "$or": [
                {"node_one_id": drug, "node_two_type": DISEASE_TYPE},
                {"node_two_id": drug, "node_one_type": DISEASE_TYPE},
            ],
        }
        out: list[TreatmentEdge] = []
        for edge in self._relations.find(query, {"context": 0, "contList": 0}):
            first = str(edge.get("node_one_id")) == drug
            if not points_from(first, str(edge.get("direction") or "")):
                continue
            side = "two" if first else "one"
            try:
                score = float(edge.get("score") or 0)
            except (TypeError, ValueError):
                score = 0.0
            out.append(TreatmentEdge(
                str(edge.get(f"node_{side}_id") or ""),
                str(edge.get(f"node_{side}_name") or ""),
                score,
                _pmids(edge),
            ))
        return sorted(out, key=lambda e: (-e.score, e.disease_biokdeid))

    def mentions_in(self, pmids: Iterable[str]) -> list[Mention]:
        """Registry ids printed in these abstracts, in the order given.

        The collection stores ``pmid`` as a string in some rows and an int in
        others, so both forms are asked for. Asked in batches of
        :data:`LOOKUP_BATCH` with ``$in`` — one round trip per PMID made a
        150-submission drug-only evaluation run past 30 minutes (D31 walks up to
        400 abstracts per unit).
        """
        wanted = list(dict.fromkeys(str(p) for p in pmids))
        found: dict[str, dict[str, Any]] = {}
        for start in range(0, len(wanted), LOOKUP_BATCH):
            chunk = wanted[start:start + LOOKUP_BATCH]
            forms: list[object] = [*chunk, *(int(p) for p in chunk if p.isdigit())]
            for document in self._documents.find(
                {"pmid": {"$in": forms}},
                {"_id": 0, "pmid": 1, "abstract": 1, "articleTitle": 1, "publicationType": 1},
            ):
                found.setdefault(str(document.get("pmid")), document)
        out: list[Mention] = []
        for pmid in wanted:
            document = found.get(pmid)
            if document is None:
                continue
            title = str(document.get("articleTitle") or "")
            text = f"{document.get('abstract') or ''} {title}"
            kinds = tuple(
                str(kind) for kind in (document.get("publicationType") or [])
            )
            for nct in dict.fromkeys(NCT.findall(text)):
                out.append(
                    Mention(
                        nct_id=nct, pmid=pmid, title=title[:200],
                        publication_types=kinds,
                    )
                )
        return out

__all__ = [
    "ABSTRACT_BUDGET",
    "DISEASE_TYPE",
    "NCT",
    "NEGATIVE_CORRELATION",
    "PMID_IN_LOCATION",
    "GraphEvidenceRepository",
    "Mention",
    "TreatmentEdge",
    "points_from",
]
