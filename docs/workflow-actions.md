# Finvalda workflow actions

Choose an everyday action for common invoices, clients, products and payments.
Choose **Advanced ·** actions when you need a particular provider method or record shape.
Action IDs in parentheses remain compatible with existing workflows.

## Connect and check access

Enable FvsServicePure on a reachable HTTPS host. Add a project connection with the web-service user, password, company and separate operation/record import parameters. File attachment also needs the configured Finvalda employee. Run **Test the connection** (`test_connection`) before building the workflow.

## Create an invoice from an approved order

1. Add **Create sales invoice** (`create_sales_invoice`) and select the project connection.
2. Set **Client code** to an existing client, or **Client company code** to find an existing client. Use **Find or create client** first when the client may be missing. Set **Document number** to your source document number and **Date** to `2026-01-15`.
3. Set **Lines** to a JSON array, binding values from earlier steps as needed:

   ```json
   [{"code":"PRODUCT01","quantity":2,"price":12.5,"warehouse":"MAIN"}]
   ```

This example uses a product code already configured in Finvalda; replace it with your own. MAIN must be an existing warehouse code; a product line needs a warehouse here or in the step. Prices are before VAT. The import parameter supplies company-specific journal and accounting settings. A rerun with the same client and document number returns the existing invoice. Payments have different duplicate handling: an existing client/payment document number causes a conflict, so give each payment its own number and inspect Finvalda before retrying.

Use the purchase invoice/vendor bill action for supplier documents; check its
partner, product and tax settings before execution. File inputs accept workflow
files from earlier steps. Download actions return workflow files for later email,
archive or document steps, rather than local filesystem paths.

## Read stock, prices, balances and changes

- **Read stock levels** (`stock_levels`): select a product and the warehouse/location filters needed for the company.
- **Read product prices** (`product_prices`): read product pricing; client/pricelist options depend on the provider.
- **Read client debt** (`client_debt`): read existing debt/balance information before a follow-up workflow.
- **Read records changed since a date** (`changes_since`): choose **What** and set **Since** to `2026-01-01`.

Read subsequent pages using **Page** and **Rows per page**. Finvalda filters by date, so repeating a date repeats that day; deduplicate by record code.

**Advanced · Export a list to a file** (`export_list`) saves a GetDescriptions list as JSONL. Use its continuation outputs when an export stops early.

## Work with advanced records

**Advanced ·** description-list actions read specific record families; operation-list actions read journals and document classes. Record and document write actions accept the corresponding Finvalda fields and import parameters. Prefer an everyday action for invoices or payments. Use **Advanced · Call a Finvalda method** only when you know the exact supported service method and its parameters.

Read actions still contact the provider during a workflow test. Write actions are
suppressed in test mode. A lost response after a write can leave an unknown
outcome; inspect provider records and reported effects before running it again.
