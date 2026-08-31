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
| `schema.py` | Single source of truth for field names, column order, and allowed enum values (transaction types, statuses, currencies) plus fixed pools (merchants, source systems). Used by the dataset generator AND the deployed Lambda — see "Packaging" below. |
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
## Infrastructure

All AWS resources are provisioned with Terraform — nothing is created manually in the console. The project uses a **shared remote state backend** (S3 + DynamoDB), provisioned once via a separate [`portfolio-shared-infra`](../portfolio-shared-infra) repository rather than duplicating a state bucket per project. See that repo's README for why the backend has to be bootstrapped separately.

### Bucket Layout

One S3 bucket, three key prefixes (S3 has no real folders — these are prefixes on object keys):

| Prefix | Purpose |
|---|---|
| `incoming/` | Where a partner's daily CSV is dropped. Any `.csv` object created here triggers processing. |
| `processed/` | Successfully validated/transformed output, written per batch as `processed/{partner_batch_id}/transactions.csv` and `processed/{partner_batch_id}/summary.json`. Objects here expire automatically after 90 days via a lifecycle rule. |
| `quarantine/` | Reserved for future use; not currently written to. Rejected files are deliberately left in place in `incoming/` rather than moved — see "Filename Validation & Rejection Alerts" below. |

### Trigger Design

S3 invokes the Lambda directly on `s3:ObjectCreated:*` events, filtered to `incoming/` + `.csv` so it never fires on writes to `processed/`/`quarantine/` or on a stray non-CSV file. SQS/DLQ (required by the project brief) is intentionally reserved for *failure handling once Lambda is already running*, not as plumbing between S3 and Lambda — keeping the trigger itself as simple as possible.

### IAM

- **Lambda execution role** — least-privilege: `s3:GetObject` scoped to `incoming/*`, `s3:PutObject` scoped to `processed/*` and `quarantine/*`, `s3:ListBucket` scoped to the bucket with a `Condition` restricting it to the `processed/` prefix (needed for reprocessing detection — see below), and `sns:Publish` scoped to one specific topic ARN. No delete permission, no wildcard bucket or cross-service access.
- **`aws_lambda_permission` (S3 invoke permission)** — a separate resource-based policy from the execution role above, explicitly granting the S3 service principal permission to invoke this specific Lambda, scoped to this bucket's ARN. This is the piece that's easy to forget when wiring an S3→Lambda trigger manually — without it, the event notification silently does nothing.
- **Local development** uses a dedicated, scoped IAM user (not an admin/root account) for running Terraform.

### State Backend Note

This project intentionally uses **local state for nothing** — all state is remote, in the shared backend, from the first `apply`. The one exception in the whole portfolio is `portfolio-shared-infra` itself, which has to use local state since it's what creates the remote backend everything else depends on.

### Filename Validation & Rejection Alerts

The handler's first check validates every incoming object key against a required naming convention before anything else happens:

```
transactions_{YYYY-MM-DD}_{partner_batch_id}.csv
```

- **Matches:** proceeds to size check, staleness check, reprocessing check, then real processing.
- **Doesn't match:** the handler publishes an alert to an SNS topic (`aws_sns_topic.rejected_files`) with the bucket and key, then returns. The file is **deliberately left untouched** in `incoming/` — no automatic quarantine copy or delete.

This is a scope decision, not an oversight: automated **detection**, human-driven **remediation**. A person receives the email alert and handles the file manually via the AWS Console (rename, move, or delete as appropriate). This sidesteps needing "safe move" logic (copy-then-delete-only-on-confirmed-success) inside the handler for a case that, in practice, needs a human decision anyway.

SNS email subscriptions require a one-time manual confirmation click (sent by AWS after `terraform apply`) before delivery starts — this can't be automated by design, since it's meant to prevent subscribing an address you don't own.

### File Size Limit

The object's size (read directly from the S3 event — no extra API call needed) is checked against `MAX_FILE_SIZE_MB` (default 25MB) before the file is ever read. An oversized file is rejected through the same alert path as a bad filename, rather than the handler attempting to process something far larger than a normal daily file — an oversized file is itself an anomaly worth a human looking at, not a size Lambda should try to force through.

### Filename Date Staleness

Once a filename passes the convention check, its embedded date is compared against today's date. If it's more than 3 days in the past or future, the handler logs a `WARNING` — but this **does not block processing**. A backfilled or weekend-delayed file is a legitimate case, not a failure; the check exists to surface an anomaly for a human to glance at, not to gate the pipeline. This mirrors the critical/warning severity split already used in the dataset's defect taxonomy, just applied to the filename rather than row data.

### Reprocessing Detection

Output is written to `processed/{partner_batch_id}/`, keyed by the batch ID embedded in the filename — not a flat filename in `processed/`. This makes "has this batch already been processed" a cheap existence check (`s3:ListBucket` scoped to the `processed/` prefix, `MaxKeys=1`) rather than requiring a separate tracking mechanism.

If a file arrives for a batch that's already been processed (e.g. a corrected afternoon resubmission of a morning file), the handler logs a `WARNING`, publishes an SNS alert (reusing the rejection topic, distinct subject/message), and **still allows processing to proceed** — the write step overwrites the prior result, but never silently. This addresses the brief's "idempotency" stretch goal from a slightly different angle: not preventing duplicate processing outright, but ensuring any reprocessing is deliberate and auditable rather than invisible.

### Validation Logic (`src/lambda/validation.py`)

Two passes, run per row:

1. **Structural** — can the row even be evaluated? Required fields (`transaction_id`, `account_id`, `amount`, `transaction_date`) present, `amount` parses as a number, dates parse as real dates. Always critical. A row that fails structurally skips business-rule checks entirely — you can't check date logic on a date that doesn't parse. Note: `missing_required_field` (originally part of the defect taxonomy) is treated as structural, not business-rule, since it's about whether the row can be evaluated at all, not a judgment call about the data's meaning.
2. **Business rule** — the remaining 7 checks from the defect taxonomy above, run only against rows that passed structurally. Two of them (`duplicate_transaction`, `orphaned_refund`) need the whole batch, not just one row — the validator does a first pass over every raw `transaction_id` in the file (including rows that failed structurally) before evaluating these, so a refund pointing at a real transaction isn't wrongly flagged as orphaned just because that other row happened to fail structural validation for an unrelated reason.

**Disposition:** any critical violation rejects the row (excluded from `transactions.csv`, included in `summary.json`'s exception detail). Warning-only violations still pass but are recorded in the exception detail too, not just counted — a reviewer can see *which* rows were flagged, not just how many. `zero_value_transaction` deliberately still fires even for `failed`-status rows where a zero amount is expected by design — the check stays generic rather than special-casing it, matching how real data-quality tools typically behave (flag and let a human/downstream system decide it's expected, rather than the validator silently suppressing it).

Verified against the real seeded dataset: all 40 manifest-injected defects were detected with zero misses, and zero false positives once two bugs (see below) were fixed.

**Bugs caught during testing, not just designed around:**
- Orphan-refund checking initially only considered `transaction_id`s among rows that *passed* structural validation, which meant a legitimate refund could be wrongly flagged as orphaned if the row it pointed to happened to fail structurally for an unrelated reason (e.g. a blank `account_id`). Fixed by checking against every raw ID in the file, independent of structural pass/fail.
- `duplicate_transaction` was being appended once per row scanned per matching ID, so a row appearing twice in the file got the same violation listed twice within one row's violation list. Fixed to append exactly once per unique duplicated ID.

### Reading & Writing (`src/lambda/handler.py`)

- **Reading:** the CSV is streamed line-by-line (`response["Body"].iter_lines()`) rather than loading the whole object into memory with `.read()` — keeps memory usage proportional to what's being processed, not the full file size.
- **Writing order is deliberate, not incidental.** `transactions.csv` is written first, `summary.json` last. S3 writes aren't transactional, so if the Lambda crashed between the two, the worst case is a stale-but-present `summary.json` next to a newer `transactions.csv` — recoverable and detectable — rather than the reverse (a `summary.json` claiming success while `transactions.csv` is missing or stale). This also matters because `summary.json`'s presence is exactly what the reprocessing check above looks for via `list_objects_v2`.
- **`transactions.csv` is always written, even if empty** (header-only, for an all-rejected batch) — a consistent output shape is easier to build downstream tooling against than "sometimes this file exists."

### Where Polars Fits (and Where It Doesn't)

The brief calls for Polars as the primary processing library, but Polars isn't part of AWS's standard Lambda Python runtime the way `boto3` is — packaging it into a Lambda genuinely requires either a Lambda Layer (built against Amazon Linux's architecture, not whatever your laptop produces) or switching to a container image deployment instead of a zip. Both are real, valid patterns, but meaningfully bigger infrastructure lifts than this project's scope calls for.

**Decision:** the deployed Lambda's actual transformation/validation logic stays plain Python (stdlib `csv`, no external dependencies beyond `boto3`, which ships with the runtime) — genuinely appropriate for Lambda's execution model and easy to package with a plain zip. Polars (and the Pandas comparison) is used specifically for the benchmark report (brief item 11), run as a **standalone local script** against the large benchmark dataset, entirely separate from the deployed Lambda. This keeps "the Lambda runs reliably in production" and "we're benchmarking two libraries against each other" as two separate concerns rather than conflating them — heavier dataframe processing at real scale would belong on AWS Glue or ECS/Fargate, not Lambda, regardless of which library was used.

### Packaging

`archive_file` builds the Lambda's zip from three individual files pulled from two different directories — `handler.py` and `validation.py` from `src/lambda/`, and `schema.py` from `src/` — rather than zipping one folder wholesale. This keeps `schema.py` a single source of truth shared by the dataset generator and the Lambda, with no duplicate copy maintained on disk. All three land flat at the zip's root, since Lambda unzips into one working directory and `import schema` expects to find it right there.

### Verified

- **S3 → Lambda trigger:** uploading a CSV to `incoming/` produces a CloudWatch Logs entry confirming the Lambda received the correct bucket/key, within milliseconds of the upload.
- **Filename validation, both branches:** an incorrectly-named file produced a `WARNING` log, an SNS publish, and a real email delivered to the subscribed address with the correct bucket/key.
- **Staleness check:** a fresh (same-day) filename produced no warning; a filename dated outside the 3-day threshold correctly logged a staleness `WARNING` without blocking processing.
- **Reprocessing detection:** with prior output present under `processed/{batch_id}/`, re-uploading the same batch ID correctly logged a reprocessing `WARNING` and delivered a real "Reprocessing Detected" email with the correct batch ID.
- **End-to-end transformation, against the real seeded dataset via real S3 (not a local mock):** uploading `seeded_transactions.csv` under a valid filename produced both `transactions.csv` and `summary.json` under `processed/{batch_id}/`, with counts and exception detail matching expectations after the two bug fixes above.

### Still To Build

- Pandas vs. Polars benchmark script (standalone, run locally against the large benchmark dataset) + written comparison report
- SQS + dead-letter queue for failure handling (during/after processing, not as the S3→Lambda trigger mechanism itself)
- GitHub Actions CI (tests only) and a separate, manually-gated deploy workflow
- Formal unit/integration test suite for `validation.py` and `handler.py` (current testing has been manual, against real data, ad hoc)
