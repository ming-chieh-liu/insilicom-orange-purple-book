"""Which company names belong to one sponsor family (D39).

A **rank key** for the registry shortlist, never a filter: measured on the
2,122 known answers the drug pool reaches, the trial's lead sponsor matched the
applicant's name in only 62% — licensing (Regeneron applies, Sanofi ran the
trial), corporate families (Genentech applies, Hoffmann-La Roche registered it:
255 answers alone), cooperative groups (NCI, EORTC). So a sponsor match moves a
trial up, and a mismatch never removes it.

The families are **hand-curated** from the companies that dominate the
registry and the FDA applicant list, as of 2026; there is no authoritative
machine-readable source on this host. A missing member costs ordering only.
Merck & Co. (MSD) and Merck KGaA (EMD Serono) are deliberately separate.
"""

from __future__ import annotations

import re
from functools import lru_cache

#: Family → name fragments (lower case) that identify a member.
FAMILIES: dict[str, tuple[str, ...]] = {
    "roche": ("roche", "genentech", "chugai", "spark therapeutics", "intermune", "foundation medicine"),
    "novartis": ("novartis", "sandoz", "alcon", "avexis", "advanced accelerator applications", "the medicines company"),
    "pfizer": ("pfizer", "wyeth", "hospira", "seagen", "seattle genetics", "array biopharma", "medivation",
               "anacor", "arena pharmaceuticals", "global blood therapeutics", "biohaven", "king pharm",
               "pharmacia", "upjohn"),
    "merck_us": ("merck sharp", "msd", "merck & co", "schering-plough", "schering corp", "organon", "cubist",
                 "idenix", "inspire pharm", "acceleron", "prometheus biosciences", "imago"),
    "merck_kgaa": ("merck kgaa", "emd serono", "serono"),
    "jnj": ("janssen", "johnson & johnson", "johnson and johnson", "actelion", "ortho-mcneil", "mcneil",
            "centocor", "tibotec", "pharmacyclics janssen", "momenta"),
    "abbvie": ("abbvie", "abbott", "allergan", "pharmacyclics", "stemcentrx", "forest lab", "aptalis",
               "warner chilcott", "cerevel"),
    "bms": ("bristol-myers", "bristol myers", "celgene", "juno therapeutics", "receptos", "myokardia",
            "turning point", "mirati", "karuna", "medarex"),
    "astrazeneca": ("astrazeneca", "medimmune", "alexion", "acerta", "zeneca", "pearl therapeutics",
                    "ardea biosciences"),
    "gsk": ("glaxosmithkline", "gsk", "glaxo", "smithkline", "viiv", "tesaro", "stiefel", "human genome sciences"),
    "sanofi": ("sanofi", "genzyme", "aventis", "bioverativ", "ablynx", "principia", "provention",
               "kadmon", "translate bio", "sanofi pasteur"),
    "takeda": ("takeda", "shire", "millennium pharm", "baxalta", "ariad", "tigenix", "dyax", "viropharma"),
    "lilly": ("eli lilly", "lilly", "loxo oncology", "imclone", "dermira", "verve therapeutics"),
    "amgen": ("amgen", "onyx pharm", "horizon therapeutics", "horizon pharma", "immunex", "chemocentryx",
              "kai pharm", "savient"),
    "gilead": ("gilead", "kite pharma", "immunomedics", "pharmasset", "forty seven", "cymabay"),
    "novo": ("novo nordisk",),
    "bayer": ("bayer",),
    "boehringer": ("boehringer",),
    "astellas": ("astellas", "fujisawa", "yamanouchi", "agensys", "ocata", "iveric"),
    "daiichi": ("daiichi",),
    "eisai": ("eisai",),
    "otsuka": ("otsuka", "avanir", "astex"),
    "teva": ("teva", "cephalon", "actavis"),
    "biogen": ("biogen", "idec"),
    "regeneron": ("regeneron",),
    "vertex": ("vertex pharm",),
    "ucb": ("ucb", "schwarz pharma", "ra pharmaceuticals", "zogenix"),
    "ipsen": ("ipsen", "clementia", "epizyme"),
    "jazz": ("jazz pharm", "gw pharm", "gw research"),
    "beigene": ("beigene",),
}

_STOP = {"inc", "llc", "ltd", "co", "corp", "corporation", "company", "pharma", "pharmaceuticals",
         "pharmaceutical", "pharms", "pharm", "the", "and", "of", "sa", "ag", "gmbh", "plc", "usa",
         "us", "international", "research", "development", "sub", "subsidiary", "group", "holdings",
         "therapeutics", "bio", "biotech", "limited", "lp", "global"}


@lru_cache(maxsize=None)
def family_of(name: str) -> str:
    """The family a company name belongs to, or "".

    >>> family_of("Hoffmann-La Roche"), family_of("GENENTECH INC"), family_of("Merck KGaA, Darmstadt")
    ('roche', 'roche', 'merck_kgaa')
    >>> family_of("MERCK SHARP DOHME"), family_of("Unknown Biotech")
    ('merck_us', '')
    """
    folded = " ".join(name.casefold().replace("&", " & ").split())
    for family, fragments in FAMILIES.items():
        if family == "merck_us" and "kgaa" in folded:
            continue
        if any(fragment in folded for fragment in fragments):
            return family
    return ""


def same_sponsor(applicant: str, trial_sponsor: str) -> bool:
    """Whether a trial's lead sponsor is the applicant or of its family.

    >>> same_sponsor("GENENTECH INC", "Hoffmann-La Roche"), same_sponsor("BEIGENE USA", "BeiGene")
    (True, True)
    >>> same_sponsor("REGENERON PHARMACEUTICALS", "Sanofi"), same_sponsor("", "Pfizer")
    (False, False)
    """
    if not applicant or not trial_sponsor:
        return False
    a, b = family_of(applicant), family_of(trial_sponsor)
    if a or b:
        return bool(a) and a == b
    return bool(_words(applicant) & _words(trial_sponsor))


@lru_cache(maxsize=None)
def _words(name: str) -> frozenset[str]:
    return frozenset(w for w in re.findall(r"[a-z]{3,}", name.casefold()) if w not in _STOP)


__all__ = ["FAMILIES", "family_of", "same_sponsor"]
