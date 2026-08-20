# serverless-data-processing-pipeline
A serverless AWS pipeline that ingests files, validates their structure and content, transforms the data efficiently, stores validated outputs, and reports success or failure.

# THIS IS ALL TEMPORARY AND CONSTANTLY UPDATED

## Transaction Data Schema

| Field | Type | Description |
|---|---|---|
| `transaction_id` | string | Unique identifier per row (e.g. `TXN-00001234`) |
| `partner_batch_id` | string | Identifies which simulated daily file the row came from (e.g. `BATCH-2026-08-17`) |
| `account_id` | string | Simulated customer/account reference |
| `transaction_date` | date | Date the transaction occurred |
| `posted_date` | date | Date the transaction settled (should be >= `transaction_date`) |
| `transaction_type` | enum | One of: `purchase`, `refund`, `transfer`, `adjustment`, `fee` |
| `amount` | decimal | Signed amount — negative for refunds/adjustments, positive for purchases |
| `currency` | enum | One of: `USD`, `CAD`, `EUR` |
| `status` | enum | One of: `completed`, `pending`, `failed`, `reversed` |
| `merchant_name` | string | Merchant/vendor name, drawn from a fixed pool |
| `related_transaction_id` | string (nullable) | Populated only for refunds; should reference a valid `transaction_id` |
| `source_system` | string | Upstream system the partner pulled the record from |


## Seeded Defect Taxonomy

| Defect | Field(s) Involved | Description | Severity |
|---|---|---|---|
| Missing required field | `account_id`, `amount`, `transaction_date` | One of these core fields is null/blank | Critical |
| Duplicate transaction | `transaction_id` | Same ID appears more than once, possibly across two `partner_batch_id`s | Critical |
| Invalid enum value | `status`, `currency` | Value falls outside the defined allowed set | Warning |
| Date logic violation | `transaction_date`, `posted_date` | `posted_date` precedes `transaction_date`, or `transaction_date` is in the future | Critical |
| Orphaned refund | `related_transaction_id` | Value doesn't match any real `transaction_id` in the file | Critical |
| Amount sign mismatch | `amount`, `transaction_type` | Positive amount on a `refund`, or negative amount on a `purchase` | Warning |
| Status/amount inconsistency | `status`, `amount` | `status` is `failed` but `amount` is nonzero | Warning |
| Zero-value transaction | `amount` | `amount` equals `0.00` | Warning |

## Dataset Generation

Two datasets are generated from the same shared code, so they can never drift out of structural sync:

- **Seeded dataset** (`data/sample/seeded_transactions.csv` + `data/sample/seeded_defect_manifest.json`) — a small, realistic-sized file standing in for a single daily partner drop. A known set of rows have intentionally injected defects (from the taxonomy above), and a JSON manifest records exactly which `transaction_id` got which defect and severity. This is the ground truth the validation logic and tests are checked against, and what actually flows through the pipeline in demos.
- **Benchmark dataset** (`data/sample/benchmark_transactions.csv`, not committed — see `.gitignore`) — a large, essentially clean dataset that exists only to compare Pandas vs. Polars performance. Realism of individual rows matters far less here than volume and schema consistency. Regenerate it locally with the command below; it isn't part of the repo because of its size.

### Source Files (`src/`)

| File | Purpose |
|---|---|
| `schema.py` | Single source of truth for field names, column order, and allowed enum values (transaction types, statuses, currencies) plus fixed pools (merchants, source systems). Both generators import from here so nothing drifts out of sync. |
| `clean_record.py` | Generates one fully valid transaction row. Field generation is sequenced (type → amount sign/magnitude → status → dates) because later fields depend on earlier ones. Uses weighted distributions rather than uniform randomness — e.g. ~90% `completed` status, log-normal amount magnitudes — so the data reads as realistic rather than uniformly random. |
| `defects.py` | Defect taxonomy implemented as a registry: each defect is a `(name, severity, mutation function)` entry rather than a hardcoded branch. Adding a new defect type means adding one entry to `DEFECT_REGISTRY` — nothing else needs to change. |
| `generate_dataset.py` | CLI entry point tying it together. Builds a batch of clean rows, resolves refund linkage in a second pass, and either injects seeded defects (writing a manifest alongside the CSV) or generates the large clean benchmark set. |

### Design Decisions Worth Noting

- **`related_transaction_id` is resolved in a second pass, not inline.** A refund's `related_transaction_id` needs to reference a real `transaction_id` — but that ID may not exist yet if rows are generated one at a time. `generate_dataset.py` generates the full batch first, then a `_resolve_refund_links()` pass points every refund row at a real, already-generated non-refund `transaction_id`. Only `transaction_type == "refund"` rows ever get this field populated — every other type stays blank.
- **Duplicate defects are appended, not overwritten.** Simulating a partner resending a row means the original row stays intact and a second copy (same `transaction_id`) is added to the file — not a single row mutated in place.
- **A fixed random seed makes generation reproducible.** `--seed` pins Python's PRNG so re-running the generator produces the identical dataset every time — the same "random" sequence, not a new one — which is what lets a reviewer regenerate the exact sample data from the command below rather than just trusting a static file in the repo.

### Usage

```bash
# Seeded dataset (small, defect-labeled — committed to the repo)
python src/generate_dataset.py --mode seeded --rows 2000 --defect-rate 0.05 --seed 42

# Benchmark dataset (large, mostly clean — regenerate locally, not committed)
python src/generate_dataset.py --mode benchmark --rows 500000 --seed 42
```