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
TYPES: tuple[str, ...] = (DRUG, CHEMICAL)

BATCH: int = 1_000


class Node(NamedTuple):
    """One iKraph2 entity, as a drug lookup returns it."""

    biokdeid: str
    type: str
    official_name: str
    db_id: str
    mention_count: int


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


__all__ = ["BATCH", "CHEMICAL", "DRUG", "Ikraph2Entities", "Node", "TYPES", "connection_uri"]
