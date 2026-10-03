# Primary evidence snapshots

`sources.json` is a list of records with `id`, `url` (HTTPS primary source), `kind`
(`primary_authority`, `manufacturer`, `store_policy`, `product_catalog`), `snapshot`
(UTF-8 file relative to this directory), `sha256`, `retrieved_at`, and `expires_at`
(timezone-aware ISO dates). Check scope/product/conditions before adding a source.
Use short expiry for inventory or store-policy facts. An unchanged snapshot hash proves
integrity, not truth; the independent factual reviewer checks support and coverage.

Empty sources are allowed for ordinary editorial advice. Consequential factual claims
without matching evidence become `review_required`; there is no universal word blacklist
or mandatory human approval of every article. Supply primary evidence or remove/rewrite
the flagged claim, then rerun validation. Do not fabricate source records to pass.
Snapshots are never fetched from model-supplied URLs during publication.
