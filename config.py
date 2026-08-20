"""Minimal configuration shim for the analysis modules.

The modules in `analysis/` are reproduced here BYTE-IDENTICAL to the versions
that produced the results in the README -- they are the record, not a rewrite.
Each of them imports `config` for exactly one value, the path to the recorded
database, so this file supplies that and nothing else.

The full application's config carried scanner thresholds, exchange settings and
env-var lookups for API credentials. None of that is needed to re-run the
analysis, so none of it is reproduced here.
"""

# The recorded alert database. Not distributed: it contains Telegram chat ids
# (personal data) and is 12 GB. See "Reproducing this" in the README for the
# schema the modules expect, so the analysis can be pointed at your own
# recording instead.
DB_PATH = "market_memory.db"
