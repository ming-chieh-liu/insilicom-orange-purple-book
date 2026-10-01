"""iKraph2 literature edges and their PMIDs, through the iKraph2 API (D42).

Two reads, both ``POST`` JSON to ``/v1/…``:

* ``search_direct_relations`` in **paging mode** (``page_size`` + ``cursor``):
  every relation from a drug to a ``Disease`` entity, each visited once;
* ``search_direct_relations_evidences``: the papers behind one relation,
  highest mention confidence first, paged by mention rows.

**Evidence is the expensive read.** A cold page costs ~10 ms per mention row
(pembrolizumab–NSCLC, 4,027 mentions: ~10 s a page of 1,000), so PMIDs are
fetched only up to the abstract budget the caller will actually read, best
evidence first — never the whole edge.

:class:`Ikraph2Literature` puts these behind the interface S7 already uses
(:class:`dao.graph_evidence.GraphEvidenceRepository`: ``pmids_for``,
``treatment_edges``, ``mentions_in``, ``abstracts``); the abstracts themselves
still come from iKraph1's ``pubmedDocs`` (a PMID is the same paper in both).

Every reply must carry the pinned ``kg_release``; any other is refused.
One client per process, built in ``main``; the HTTP opener is injectable.
"""

from __future__ import annotations

import json
import random
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from config.ikraph2 import Ikraph2Connection
from dao.graph_evidence import ABSTRACT_BUDGET, Abstract, Mention, TreatmentEdge
from utility.logging import get_logger

_log = get_logger(__name__)

#: The relation iKraph2 names for "the drug treats the disease" — iKraph1's
#: ``relationship_type = 3`` (negative correlation) under D31.
TREATS = "treats"
DISEASE = "Disease"
#: The largest page of relations the API serves in paging mode.
RELATION_PAGE: int = 3_000
#: Evidence page bounds, in mention rows (the API's own maximum is 1,000).
EVIDENCE_PAGE_MIN: int = 100
EVIDENCE_PAGE_MAX: int = 1_000

Opener = Callable[..., Any]


class Ikraph2Error(RuntimeError):
    """The API refused a request, could not be reached, or answered another release."""


class Edge(NamedTuple):
    """One relation from a drug to a disease."""

    edge_id: str
    relation: str
    disease: str          # the disease's biokdeid
    disease_name: str
    score: float
    document_count: int
    drug_is_subject: bool


class Ikraph2Api:
    """Relations and evidence for one release, through one injected opener."""

    def __init__(self, connection: Ikraph2Connection,
                 opener: Opener = urllib.request.urlopen,
                 sleep: Callable[[float], None] = time.sleep,
                 cache_dir: Path | None = None) -> None:
        """``cache_dir`` keeps every answer on disk, append-only, so a re-run
        (or a resumed one) asks the API only for what it has not seen. A
        release's data never changes, so the directory is named for it and
        only answers carrying that release are ever written to it."""
        self._connection = connection
        self._open = opener
        self._sleep = sleep
        self._edges: dict[str, list[Edge]] = {}
        self._pmids: dict[str, tuple[int, list[str], bool]] = {}
        self._lock = threading.Lock()
        self._cache = cache_dir / f"ikraph2_cache_{connection.release}" if cache_dir else None
        if self._cache is not None:
            self._load()

    def _load(self) -> None:
        assert self._cache is not None
        edges_file, evidence_file = self._cache / "edges.jsonl", self._cache / "evidence.jsonl"
        for line in _lines(edges_file):
            self._edges[line["drug"]] = [Edge(*row) for row in line["edges"]]
        for line in _lines(evidence_file):
            held = self._pmids.get(line["edge"])
            if held is None or line["limit"] >= held[0] or line["exhausted"]:
                self._pmids[line["edge"]] = (line["limit"], line["pmids"], line["exhausted"])
        _log.info("iKraph2 cache %s: %d drugs, %d edges' evidence", self._cache,
                  len(self._edges), len(self._pmids))

    def _keep(self, name: str, record: dict[str, Any]) -> None:
        if self._cache is None:
            return
        with self._lock:
            self._cache.mkdir(parents=True, exist_ok=True)
            with (self._cache / name).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")

    def _post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        """One call, retried with backoff on 429, 5xx and network errors."""
        data = json.dumps(body).encode("utf-8")
        last = ""
        for attempt in range(self._connection.attempts):
            request = urllib.request.Request(
                f"{self._connection.api_url}/v1/{endpoint}", data=data, method="POST",
                headers={"Content-Type": "application/json"})
            try:
                with self._open(request, timeout=self._connection.http_timeout_s) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                last = f"HTTP {error.code}"
                if error.code != 429 and error.code < 500:
                    raise Ikraph2Error(f"{endpoint}: {last}") from error
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                last = str(error)
            else:
                if not isinstance(payload, dict):
                    raise Ikraph2Error(f"{endpoint}: unexpected reply")
                release = payload.get("kg_release")
                if release != self._connection.release:
                    raise Ikraph2Error(f"{endpoint}: release {release!r}, "
                                       f"expected {self._connection.release!r}")
                return payload
            self._sleep(2.0 * (attempt + 1) + random.random())
        raise Ikraph2Error(f"{endpoint}: {last} after {self._connection.attempts} attempts")

    def disease_edges(self, drug: str) -> list[Edge]:
        """Every relation between ``drug`` and a disease, best-scored first. Cached."""
        if drug in self._edges:
            return self._edges[drug]
        edges: list[Edge] = []
        cursor: Any = None
        while True:
            body: dict[str, Any] = {"biokdeid": int(drug), "other_types": [DISEASE],
                                    "page_size": RELATION_PAGE}
            if cursor is not None:
                body["cursor"] = cursor
            page = self._post("search_direct_relations", body)
            for row in page.get("relations") or []:
                edges.append(Edge(
                    edge_id=str(row.get("edge_id") or ""),
                    relation=str(row.get("relation") or ""),
                    disease=str(row.get("other_id") or ""),
                    disease_name=str(row.get("other_name") or ""),
                    score=float(row.get("score") or 0.0),
                    document_count=int(row.get("document_count") or 0),
                    drug_is_subject=bool(row.get("anchor_is_subject")),
                ))
            cursor = page.get("next_cursor")
            if cursor is None:
                break
        edges.sort(key=lambda e: (-e.score, e.edge_id))
        self._edges[drug] = edges
        self._keep("edges.jsonl", {"drug": drug, "edges": [list(e) for e in edges]})
        return edges

    def edge_pmids(self, edge_id: str, limit: int) -> list[str]:
        """Up to ``limit`` distinct PMIDs behind one edge, best evidence first. Cached."""
        if limit <= 0:
            return []
        held = self._pmids.get(edge_id)
        if held is not None and (held[0] >= limit or held[2]):
            return held[1][:limit]
        pmids: dict[str, None] = {}
        cursor: Any = None
        size = max(EVIDENCE_PAGE_MIN, min(EVIDENCE_PAGE_MAX, 2 * limit))
        exhausted = False
        while len(pmids) < limit:
            body: dict[str, Any] = {"edge_id": edge_id, "page_size": size}
            if cursor is not None:
                body["cursor"] = cursor
            page = self._post("search_direct_relations_evidences", body)
            for document in page.get("documents") or []:
                if document.get("pmid") is not None:
                    pmids.setdefault(str(document["pmid"]), None)
            cursor = (page.get("paging") or {}).get("next_cursor")
            if cursor is None:
                exhausted = True
                break
        found = list(pmids)
        self._pmids[edge_id] = (limit, found, exhausted)
        self._keep("evidence.jsonl", {"edge": edge_id, "limit": limit, "pmids": found,
                                      "exhausted": exhausted})
        return found[:limit]


def _lines(path: Path) -> list[dict[str, Any]]:
    """The JSON lines of a cache file; a torn last line (an interrupted write) is skipped."""
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for text in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(text))
        except ValueError:
            _log.warning("%s: skipped an unreadable cache line", path)
    return out


class Documents(Protocol):
    """What :class:`Ikraph2Literature` needs from iKraph1's abstract store."""

    def mentions_in(self, pmids: Iterable[str],
                    resolve: Callable[[str], list[tuple[str, str]]] | None = None
                    ) -> list[Mention]: ...

    def abstracts(self, pmids: Iterable[str]) -> list[Abstract]: ...


class Ikraph2Literature:
    """S7's literature source: iKraph2 edges and PMIDs, iKraph1 abstracts."""

    def __init__(self, api: Ikraph2Api, documents: Documents,
                 budget: int = ABSTRACT_BUDGET, workers: int = 4) -> None:
        self._api = api
        self._documents = documents
        self._budget = budget
        self._workers = workers
        self._treatments: dict[str, list[TreatmentEdge]] = {}

    def prefetch(self, pairs: Iterable[tuple[str, str]]) -> None:
        """Warm the caches for these (drug, disease) pairs, ``workers`` drugs at once.

        Grouped by drug so no two threads ever fetch the same drug's edges; the
        API runs four workers, and a cold evidence page takes seconds.
        """
        by_drug: dict[str, list[str]] = defaultdict(list)
        for drug, disease in dict.fromkeys(pairs):
            by_drug[drug].append(disease)
        self._fan_out(sorted(by_drug), lambda d: [self.pmids_for(d, x) for x in by_drug[d]],
                      "pairs of")

    def prefetch_treatments(self, drugs: Iterable[str]) -> None:
        """Warm :meth:`treatment_edges` for these drugs, ``workers`` at once."""
        self._fan_out(sorted(set(drugs)), self.treatment_edges, "treats walks of")

    def _fan_out(self, drugs: list[str], work: Callable[[str], object], label: str) -> None:
        done = [0]
        lock = threading.Lock()
        started = time.monotonic()

        def one(drug: str) -> None:
            try:
                work(drug)
            except Ikraph2Error as error:
                # Left cold: the serial pass asks again and fails loudly there.
                _log.warning("iKraph2 prefetch, drug %s: %s", drug, error)
            with lock:
                done[0] += 1
                if done[0] % 100 == 0 or done[0] == len(drugs):
                    rate = done[0] / max(time.monotonic() - started, 1e-9)
                    _log.info("iKraph2 prefetch: %s %d/%d drugs, ~%.0f min left", label,
                              done[0], len(drugs), (len(drugs) - done[0]) / rate / 60)

        with ThreadPoolExecutor(max_workers=max(1, self._workers)) as executor:
            list(executor.map(one, drugs))

    def pmids_for(self, drug: str, disease: str) -> list[str]:
        """PMIDs behind every relation between the two, best edge first, up to the budget.

        Every relation type counts, as iKraph1's ``pmids_for`` did: an abstract
        reporting a trial's adverse events still names the trial.
        """
        found: dict[str, None] = {}
        for edge in self._api.disease_edges(drug):
            if edge.disease != disease:
                continue
            # Each edge is asked for the whole budget: its best papers may
            # already have come from a stronger edge of the same pair.
            for pmid in self._api.edge_pmids(edge.edge_id, self._budget):
                found.setdefault(pmid, None)
                if len(found) >= self._budget:
                    return list(found)
        return list(found)

    def treatment_edges(self, drug: str) -> list[TreatmentEdge]:
        """The drug's ``treats`` edges to diseases, best-scored first (D31).

        PMIDs are fetched in score order until the abstract budget is reached;
        later edges carry none, since the walk would not read them.
        """
        if drug in self._treatments:
            return self._treatments[drug]
        out: list[TreatmentEdge] = []
        seen: set[str] = set()
        for edge in self._api.disease_edges(drug):
            if edge.relation != TREATS or not edge.drug_is_subject:
                continue
            pmids: tuple[str, ...] = ()
            if len(seen) < self._budget:
                pmids = tuple(self._api.edge_pmids(edge.edge_id, self._budget - len(seen)))
                seen.update(pmids)
            out.append(TreatmentEdge(edge.disease, edge.disease_name, edge.score, pmids))
        self._treatments[drug] = out
        return out

    def mentions_in(self, pmids: Iterable[str],
                    resolve: Callable[[str], list[tuple[str, str]]] | None = None
                    ) -> list[Mention]:
        return self._documents.mentions_in(pmids, resolve)

    def abstracts(self, pmids: Iterable[str]) -> list[Abstract]:
        return self._documents.abstracts(pmids)


__all__ = ["DISEASE", "Documents", "Edge", "Ikraph2Api", "Ikraph2Error", "Ikraph2Literature",
           "TREATS"]
