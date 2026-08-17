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