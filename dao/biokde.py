"""Read access to the Insilicom knowledge graph, through one pooled client.

The graph is used here for one thing: as a **synonym dictionary**. Its
``pubmedEntitiesFlat`` collection holds one row per (name, entity), so a brand
name, an INN, a sponsor code name and a misspelling all resolve to the same
prefixed id — ``KEYTRUDA``, ``Pembrolizumab`` and ``MK3475`` are three of the
sixteen names carrying ``MESH:C582435``. That is what lets the FDA side and the
ClinicalTrials.gov side meet in one id space.

**Pooling and injection.** :class:`pymongo.MongoClient` *is* the connection
pool. One is built in ``main`` and handed to :class:`BiokdeRepository`, which
never builds its own — so a process opens exactly one pool, and a caller that
wants a fake passes one. Nothing below this module knows a host or a password.

**Case sensitivity is the trap.** A plain equality query on ``name`` is
case-sensitive: ``keytruda`` returns nothing while ``Keytruda`` returns the
drug. The collection carries a ``name_1_type_1_ci`` index built under an
``{en, strength 2}`` collation, and every query here passes that collation —
both so casing stops mattering and so the index is actually used. Measured:
12–215 ms per lookup, 37 ms for a batch of four.

Read-only by construction: this module has no write path.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Iterable, Iterator
from typing import Any, NamedTuple

from config.settings import BiokdeConnection
from utility.logging import get_logger

_log = get_logger(__name__)

#: How many names to ask for in one ``$in``. Large enough that 2,700 drug
#: names cost a handful of round trips, small enough that a query document
#: stays well inside the 16 MB limit.
BATCH: int = 1_000


class Entity(NamedTuple):
    """One entity the graph knows, as a name resolves to it."""

    #: Prefixed identifier: ``MESH:D016190``, ``MESH:C582435``,
    #: ``PubChem:CID56843331``. The prefix is part of the id, not decoration —
    #: not every drug has a MeSH record.
    id: str
    #: The graph's **internal** node id, and the one ``pubmedRelations`` keys
    #: its edges on. The prefixed ``id`` above is the external vocabulary id and
    #: does **not** appear in the edge collection, so anything that wants to
    #: walk from a drug to its supporting literature needs this. Kept here so
    #: P1 resolves it once rather than every later query re-deriving it.
    biokdeid: str
    official_name: str
    #: ``Chemical``, ``Disease``, ``Gene``, ``Species`` …
    type: str
    #: ``Drug``, ``Rare Disease``, or ``NA``.
    subtype: str
    #: How often this **(name, entity) pair** was seen — the graph's own
    #: confidence in the synonym, not the entity's overall frequency. The same
    #: node carries wildly different counts under different spellings:
    #: ``MESH:D012964`` is reached by ``Sodium`` at 349,700 and by
    #: ``Lenacapavir Sodium`` at **3**, which is a bad synonym row asserting
    #: that a 2022 antiretroviral *is* sodium.
    #:
    #: Reading it is what separates a canonical name from an artefact. Without
    #: it the two look identical and the vocabulary preference then picks the
    #: artefact, because the counter-ion has a clean MeSH descriptor while the
    #: newer drug has only a PubChem id. Same shape as ``Control`` reaching
    #: ``Chlordiazepoxide`` at cnt 2 against 5,419.
    cnt: int = 0


def connection_uri(connection: BiokdeConnection) -> str:
    """The MongoDB URI, with the credentials file's user and password.

    The file names ``localhost``; the host comes from ``connection`` because
    that is what the credentials actually authenticate against.
    """
    payload = json.loads(connection.credentials.read_text())
    user = urllib.parse.quote_plus(str(payload["username"]))
    password = urllib.parse.quote_plus(str(payload["password"]))
    return (
        f"mongodb://{user}:{password}@{connection.host}:{connection.port}"
        f"/?authSource={connection.auth_source}"
    )


class BiokdeRepository:
    """Name → entity lookups against one injected, pooled client."""

    def __init__(self, client: Any, connection: BiokdeConnection) -> None:
        """Take the pool; never open one.

        ``client`` is a :class:`pymongo.MongoClient` or anything with the same
        ``client[db][collection].find`` shape, which is what the tests pass.
        """
        self._entities = client[connection.database][connection.entities]
        locale, strength = connection.collation
        self._collation = {"locale": locale, "strength": strength}

    def resolve(self, names: Iterable[str]) -> dict[str, tuple[Entity, ...]]:
        """Every entity each name resolves to, case-insensitively.

        Keyed by the name **as asked for**, not as stored, so a caller can look
        its own string back up. A name resolving to nothing is absent from the
        result rather than present and empty.
        """
        wanted = [name for name in dict.fromkeys(names) if name]
        found: dict[str, list[Entity]] = {}
        folded: dict[str, list[str]] = {}
        for name in wanted:
            folded.setdefault(name.casefold(), []).append(name)
        for batch in _batched(wanted, BATCH):
            for row in self._entities.find(
                {"name": {"$in": batch}},
                {
                    "_id": 0,
                    "name": 1,
                    "id": 1,
                    "biokdeid": 1,
                    "official name": 1,
                    "type": 1,
                    "subtype": 1,
                    "cnt": 1,
                },
            ).collation(self._collation):
                entity = Entity(
                    id=str(row.get("id") or ""),
                    biokdeid=str(row.get("biokdeid") or ""),
                    official_name=str(row.get("official name") or ""),
                    type=str(row.get("type") or ""),
                    subtype=str(row.get("subtype") or ""),
                    cnt=int(row.get("cnt") or 0),
                )
                if not entity.id:
                    continue
                # The match is case-insensitive, so the stored spelling may
                # differ from what was asked. Credit every asked-for spelling
                # that folds to the same string.
                for asked in folded.get(str(row.get("name") or "").casefold(), []):
                    bucket = found.setdefault(asked, [])
                    if entity not in bucket:
                        bucket.append(entity)
        _log.info("resolved %d of %d names", len(found), len(wanted))
        return {name: tuple(entities) for name, entities in found.items()}

    def synonyms(self, ids: Iterable[str]) -> dict[str, frozenset[str]]:
        """Every name recorded against each entity id.

        The expansion that makes matching work in the other direction: given
        the MeSH id an FDA ingredient resolves to, this is every spelling a
        trial might have used for it.
        """
        wanted = [identifier for identifier in dict.fromkeys(ids) if identifier]
        out: dict[str, set[str]] = {}
        for batch in _batched(wanted, BATCH):
            for row in self._entities.find(
                {"id": {"$in": batch}}, {"_id": 0, "id": 1, "name": 1}
            ):
                name = str(row.get("name") or "")
                if name:
                    out.setdefault(str(row["id"]), set()).add(name)
        _log.info("%d of %d ids have synonyms", len(out), len(wanted))
        return {identifier: frozenset(names) for identifier, names in out.items()}


    def internal_ids(self, ids: Iterable[str]) -> dict[str, str]:
        """External vocabulary id → the graph's internal ``biokdeid``.

        Needed for entities that arrive as an id rather than as a name:
        indications come out of ``indications.tsv`` already coded to MeSH, so
        they are never resolved through :meth:`resolve` and would otherwise
        carry no internal id at all — leaving P5 unable to walk the disease end
        of a drug-disease edge.
        """
        wanted = [identifier for identifier in dict.fromkeys(ids) if identifier]
        out: dict[str, str] = {}
        for batch in _batched(wanted, BATCH):
            for row in self._entities.find(
                {"id": {"$in": batch}}, {"_id": 0, "id": 1, "biokdeid": 1}
            ):
                internal = str(row.get("biokdeid") or "")
                if internal:
                    out.setdefault(str(row["id"]), internal)
        _log.info("%d of %d ids have an internal id", len(out), len(wanted))
        return out


def _batched(items: list[str], size: int) -> Iterator[list[str]]:
    """``items`` in chunks of ``size``; nothing for an empty list.

    >>> list(_batched(["a", "b", "c"], 2))
    [['a', 'b'], ['c']]
    >>> list(_batched([], 2))
    []
    """
    for start in range(0, len(items), size):
        yield items[start : start + size]


__all__ = ["BATCH", "BiokdeRepository", "Entity", "connection_uri"]
