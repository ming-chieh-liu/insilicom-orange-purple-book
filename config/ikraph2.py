"""Where iKraph2 lives (D42). No client is built here.

Two doors into one release:

* the **API** (``api_url``) for graph reads — a drug's disease edges and the
  PMIDs behind an edge;
* the **Mongo store** behind it (``mongo_host``) for the one read the API does
  not offer: an entity by its exact external id (``db_id``). Read-only.

Every reply and every collection is pinned to ``release``; a reply from any
other release is refused, not mixed in.

Kept out of :mod:`config.settings` on purpose, like :mod:`config.agent_pool`:
every stage imports that module, while only S2 and S7 read this one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Ikraph2Connection:
    """The API address, the Mongo store, the release, and the credentials file."""

    api_url: str = "http://127.0.0.1:8971"
    release: str = "v1_2"
    mongo_host: str = "jupiter2"
    mongo_port: int = 27017
    auth_source: str = "admin"
    database: str = "biokde"
    #: The user's own Mongo account (``username`` / ``password``), gitignored.
    credentials: Path = Path("creds.json")
    timeout_ms: int = 30_000
    #: Seconds one API call may take; a cold evidence page reads ~10 ms per row.
    http_timeout_s: float = 240.0
    #: API calls retried on 429, 5xx and network errors.
    attempts: int = 4

    @property
    def entities(self) -> str:
        """The entity collection of this release."""
        return f"ikraph2_entities_{self.release}"

    @property
    def has_credentials(self) -> bool:
        return self.credentials.is_file()


__all__ = ["Ikraph2Connection"]
