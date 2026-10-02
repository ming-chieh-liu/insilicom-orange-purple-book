"""iKraph2 entities by exact external id, through one pooled client (D42).

The iKraph2 API resolves **names** only. The pipeline needs the opposite: the
entity whose ``db_id`` is exactly a UNII, MeSH id or PubChem CID it already
holds. The entity collection is indexed on ``db_id``, so this reads it
directly — read-only, one batched ``$in`` per thousand ids.

Each iKraph2 entity carries **one** external id (measured 2026-10-01): drug
entities (``TherapyPreventions``) a UNII, ChEBI or RxCUI; ``Chemical``
entities a MeSH id or a PubChem CID. Only those two types are read.

**Pooling and injection.** ``client`` is a :class:`pymongo.MongoClient` built
once in ``main`` and passed in; nothing here opens a connection.
"""

from __future__ import annotations

import json
import urllib.parse
from collections import defaultdict
from collections.abc import Iterable
from typing import Any, NamedTuple

from config.ikraph2 import Ikraph2Connection
from utility.logging import get_logger

_log = get_logger(__name__)

#: The two entity types a drug can be, preferred first (the user's rule).
DRUG = "TherapyPreventions"
CHEMICAL = "Chemical"
DISEASE = "Disease"
TYPES: tuple[str, ...] = (DRUG, CHEMICAL)

BATCH: int = 1_000


class Node(NamedTuple):
    """One iKraph2 entity, as a drug lookup returns it."""

    biokdeid: str
    type: str
    official_name: str
    db_id: str
    mention_count: int
    #: How iKraph2 lists the name looked up: ``canonical`` (the entity's own
    #: name) or ``alias``; "" where the node was read by id (D45).
    role: str = ""


def connection_uri(connection: Ikraph2Connection) -> str:
    """The Mongo URI, with the credentials file's user and password."""
    payload = json.loads(connection.credentials.read_text())
    user = urllib.parse.quote_plus(str(payload["username"]))
    password = urllib.parse.quote_plus(str(payload["password"]))
    return (f"mongodb://{user}:{password}@{connection.mongo_host}:{connection.mongo_port}"
            f"/?authSource={connection.auth_source}")


class Ikraph2Entities:
    """Exact ``db_id`` → entity lookups against one injected client."""

    def __init__(self, client: Any, connection: Ikraph2Connection) -> None:
        self._entities = client[connection.database][connection.entities]
        self._names = client[connection.database][connection.entity_names]

    def _db_ids(self, biokdeids: Iterable[int]) -> dict[int, tuple[str, int]]:
        """biokdeid → (db_id, mention_count)."""
        wanted = sorted(set(biokdeids))
        out: dict[int, tuple[str, int]] = {}
        for start in range(0, len(wanted), BATCH):
            for row in self._entities.find(
                {"biokdeid": {"$in": wanted[start:start + BATCH]}},
                {"_id": 0, "biokdeid": 1, "db_id": 1, "mention_count": 1},
            ):
                out[int(row["biokdeid"])] = (str(row.get("db_id") or ""),
                                             int(row.get("mention_count") or 0))
        return out

    def named(self, keys: Iterable[str], types: Iterable[str]) -> dict[str, list[Node]]:
        """Exact name key (:func:`source.unii_mesh.exact_key`) → every entity of
        ``types`` iKraph2 records under exactly that name (D44), with whether
        the name is the entity's canonical one or an alias (D45)."""
        wanted = sorted({k for k in keys if k})
        kinds = list(types)
        hits: dict[str, list[tuple[int, str, str, str]]] = defaultdict(list)
        for start in range(0, len(wanted), BATCH):
            for row in self._names.find(
                {"term_key": {"$in": wanted[start:start + BATCH]}, "type": {"$in": kinds}},
                {"_id": 0, "term_key": 1, "biokdeid": 1, "type": 1, "role": 1},
            ):
                hits[str(row["term_key"])].append(
                    (int(row["biokdeid"]), str(row.get("type") or ""), str(row["term_key"]),
                     str(row.get("role") or "")))
        meta = self._db_ids(b for found in hits.values() for b, _, _, _ in found)
        out: dict[str, list[Node]] = {}
        for key, found in hits.items():
            nodes = {b: Node(str(b), kind, name, meta.get(b, ("", 0))[0],
                             meta.get(b, ("", 0))[1], role)
                     for b, kind, name, role in found}
            out[key] = sorted(nodes.values(), key=lambda n: (
                kinds.index(n.type) if n.type in kinds else len(kinds), -n.mention_count,
                n.biokdeid))
        _log.info("iKraph2: %d of %d names known", len(out), len(wanted))
        return out

    def diseases_by_db_id(self, ids: Iterable[str]) -> dict[str, list[str]]:
        """External id → the biokdeids of every ``Disease`` entity carrying exactly it."""
        wanted = [i for i in dict.fromkeys(ids) if i]
        out: dict[str, list[str]] = defaultdict(list)
        for start in range(0, len(wanted), BATCH):
            for row in self._entities.find(
                {"db_id": {"$in": wanted[start:start + BATCH]}, "type": DISEASE},
                {"_id": 0, "biokdeid": 1, "db_id": 1},
            ):
                out[str(row["db_id"])].append(str(row["biokdeid"]))
        return {k: sorted(v) for k, v in out.items()}

    def internal_ids(self, ids: Iterable[str]) -> dict[str, str]:
        """External id → the one ``Disease`` entity carrying exactly it (S5);
        an id two disease entities carry is left out."""
        return {k: v[0] for k, v in self.diseases_by_db_id(ids).items() if len(v) == 1}

    def by_db_id(self, ids: Iterable[str]) -> dict[str, list[Node]]:
        """Every drug or chemical entity whose ``db_id`` is one of ``ids``.

        Keyed by the id asked for; an id no entity carries is absent. Within an
        id, drug entities come first, then by mention count.
        """
        wanted = [i for i in dict.fromkeys(ids) if i]
        out: dict[str, list[Node]] = defaultdict(list)
        for start in range(0, len(wanted), BATCH):
            for row in self._entities.find(
                {"db_id": {"$in": wanted[start:start + BATCH]}, "type": {"$in": list(TYPES)}},
                {"_id": 0, "biokdeid": 1, "type": 1, "official_name": 1, "db_id": 1,
                 "mention_count": 1},
            ):
                out[str(row["db_id"])].append(Node(
                    biokdeid=str(row.get("biokdeid") or ""),
                    type=str(row.get("type") or ""),
                    official_name=str(row.get("official_name") or ""),
                    db_id=str(row["db_id"]),
                    mention_count=int(row.get("mention_count") or 0),
                ))
        for nodes in out.values():
            nodes.sort(key=lambda n: (TYPES.index(n.type), -n.mention_count, n.biokdeid))
        _log.info("iKraph2: %d of %d ids carried by an entity", len(out), len(wanted))
        return dict(out)


__all__ = ["BATCH", "CHEMICAL", "DISEASE", "DRUG", "Ikraph2Entities", "Node", "TYPES", "connection_uri"]
