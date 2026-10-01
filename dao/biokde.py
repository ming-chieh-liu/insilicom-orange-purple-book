"""How to reach iKraph1 (the Insilicom graph ``biokde`` on galaxy3).

Since D42 iKraph1 serves one thing: the PubMed abstracts in ``pubmedDocs``,
which :class:`dao.graph_evidence.GraphEvidenceRepository` reads for S7 (a PMID
is the same paper in iKraph1 and iKraph2). Drug identity, synonyms, disease
ids and literature edges all come from iKraph2 (:mod:`dao.ikraph2_entities`,
:mod:`dao.ikraph2_api`).

**Pooling and injection.** :class:`pymongo.MongoClient` *is* the connection
pool; ``main`` builds one per process with :func:`connection_uri` and injects
it. Nothing below this module knows a host or a password.
"""

from __future__ import annotations

import json
import urllib.parse

from config.settings import BiokdeConnection


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


__all__ = ["connection_uri"]
