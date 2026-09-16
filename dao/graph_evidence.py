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
            for location in edge.get("locations") or []:
                if not location:
                    continue
                match = PMID_IN_LOCATION.match(str(location[0]))
                if match:
                    found.add(match.group(1))
        return sorted(found)

    def mentions_in(self, pmids: Iterable[str]) -> list[Mention]:
        """Registry ids printed in these abstracts.

        One document lookup per PMID: the collection is indexed on ``pmid`` but
        stores it as a string in some rows and an int in others, so both are
        tried rather than assuming.
        """
        out: list[Mention] = []
        for pmid in pmids:
            document = self._documents.find_one(
                {"pmid": pmid},
                {"_id": 0, "abstract": 1, "articleTitle": 1, "publicationType": 1},
            )
            if document is None:
                try:
                    document = self._documents.find_one(
                        {"pmid": int(pmid)},
                        {
                            "_id": 0,
                            "abstract": 1,
                            "articleTitle": 1,
                            "publicationType": 1,
                        },
                    )
                except (TypeError, ValueError):
                    document = None
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
                        nct_id=nct, pmid=str(pmid), title=title[:200],
                        publication_types=kinds,
                    )
                )
        return out


__all__ = [
    "ABSTRACT_BUDGET",
    "NCT",
    "PMID_IN_LOCATION",
    "GraphEvidenceRepository",
    "Mention",
]
