# Finvalda for Flow Steward

See the [workflow action guide](docs/workflow-actions.md) for setup, invoice examples,
paging and advanced-action inputs.

Bring Finvalda accounting and business records into Flow Steward through the FvsServicePure web service. Everyday steps find or create clients, products and services, prepare sales and purchase invoices, register incoming or outgoing payments and work with invoice PDFs and attachments. Client searches can use business identifiers or email, while product searches can use codes or barcodes.

Read warehouse stock, reserved quantities, costs, sales and purchase prices, client-specific prices and outstanding debt. Date-based change reads expose clients, products, services, sales, purchases and payments for incremental exchange with other systems. Files move through Flow Steward storage, so a workflow can pass an invoice PDF to another document, email or archival step.

For more detailed integrations, the release includes 160 advanced operations derived from its pinned Finvalda reference. These cover description lists, operation classes, older record readers, record and document changes, locking, copying, journal moves, invoice and report generation and list exports. Combine these steps to turn shop orders into accounting documents, feed warehouse availability to a storefront or check unpaid invoices before processing a new order. Each connection carries its company and import settings; the configured Finvalda user and employee permissions control access.

Have your Finvalda administrator enable FvsServicePure on a publicly reachable HTTPS host and create a web-service user. Configure the company, Finvalda employee and separate operation and record import parameters in the project connection, then run the connection test.

## Why use it

Typical jobs it automates:

- **An order or invoice arrives** (from a shop, an e-mail, a form) → find the client and the
  products in Finvalda, create them if they are missing, and create the sales invoice with its PDF
  attached.
- **A bank statement arrives** → register each payment against the client and the invoice it
  pays.
- **Stock and prices for a shop** → read current stock by warehouse and the sales prices, and pass
  them to the shop's steps.
- **Changes to another system** → read the clients, products or operations changed since a date
  and send them on, page by page.
- **Debt control** → read a client's debt and unpaid documents, with the overdue part, before
  accepting a new order.

## How it fits into Flow Steward

- **Install** it as an extension: from the catalog, or with
  `./flow-steward extensions install --archive flowsteward.finvalda-0.1.2.zip`.
- **Connect**: on the extension's Connection page a project adds a Finvalda connection — the web
  service address, the web service user and password, the company, the two import parameters and
  the Finvalda employee. One project can hold several connections, for example one per company.
- **Build workflows**: the Finvalda steps appear in the workflow editor — the everyday steps first,
  then the `Advanced · …` steps. Each step's results (codes, records, totals, files) are inputs for
  the next steps.
- **Files** (invoice PDFs, attachments, exports) travel through Flow Steward's file storage: a step
  takes a workflow file as input or returns one.
- **Writes** to Finvalda are declared external effects: Flow Steward suppresses them in test runs,
  so a workflow can be tried without changing the books.

```
order / bank statement / schedule  →  Finvalda step  →  next steps (e-mail, shop, report …)
```

## What Finvalda needs

The company's Finvalda administrator:

- enables Finvalda's **web service module** and makes it reachable over **https** on a public host
  name, for example `https://fvs.example.lt/FvsServicePure.svc`;
- creates a **Finvalda user for the web service** and gives it the rights the steps need;
- names a **Finvalda employee** (finUser) for documents and files — a Finvalda employee, not the
  web service user;
- prepares the **import parameters** in Finvalda's FvsNETParamKonfig tool: one for operations
  (journal, operation type, series, accounts) and one for new records (accounts, VAT, unit).

The extension's **Setup Guide** page walks through each of these, and the **Connection** page
explains every field.

## Steps

Everyday steps:

- **Test the connection** — reads one client to check the address, user, password and company.
- **Find or create client** — looks a client up by code, company code, VAT code or e-mail; creates
  it when none matches.
- **Find or create product** — looks a product up by code or barcode; creates it when none matches.
- **Find or create service** — looks a service up by code; creates it when it does not exist.
- **Create sales invoice** — a sales invoice with product and service lines, VAT computed, and an
  optional PDF attached.
- **Create purchase invoice** — the same for a supplier's invoice.
- **Register payment** — money received from a client or paid to a supplier, against an invoice or
  as a prepayment.
- **Download invoice PDF** — prints a sale or purchase with an invoice form to a workflow file.
- **Attach file** — stores a workflow file in Finvalda and attaches it to an operation, a client or
  a product.
- **Read stock levels** — current stock by warehouse: quantity, quantity with reservations, cost.
- **Read product prices** — the six sales prices and the purchase price, and a client's special prices.
- **Read client debt** — a client's debt and unpaid documents, with the overdue part.
- **Read records changed since a date** — clients, products, services, sales, purchases or payments created or edited
  on or after a date, page by page.

Every documented method of the web service also has its own step, **160 `Advanced · …` steps** in
all: every list of GetDescriptions, every operation class of GetOperations, every older read
method, record and operation writes (create, change, delete, lock, copy, move to another journal),
invoice and report PDFs, the document register, recommended prices and employee permissions, an
export of any list to a file, and a call of any documented method. The **Coverage** page names
every method and list of Finvalda's documents and the step that offers it, or the reason there is
none; the **Finvalda API** page lists each step's inputs and results with their types.

## How writes behave

- **Running a step again does not create duplicates.** Everyday steps look up before they create:
  clients, products and services by code (and the other values given), invoices and payments by
  their document number for that client. When the answer is not clear — another client with the
  same company code, an invoice with another amount, a lookup row without its code — the step stops
  with a conflict and writes nothing.
- **An unknown outcome is marked, never retried.** When Finvalda's answer does not confirm that a
  change was saved (a timeout, a lost connection, a server error), the step reports the outcome as
  unknown so the workflow can check Finvalda first. A step that saves more than one thing (an
  invoice and its file) names what it already saved when a later call fails.
- **Refusals are explained.** When Finvalda refuses a change, the step passes on Finvalda's code
  with the manual's meaning; nothing was saved.
- **Money is exact.** Amounts are exact decimals from input to Finvalda and back: no rounding
  through floating point, and an amount with more than cents is refused rather than rounded.
- **The password stays secret.** It travels only in the header the web service requires and is
  removed from every answer before it is read.

## Installing and developing

Requirements: Flow Steward Core with the extension host contract `extension_host_v2`; the
extension is built on `flowsteward-extension-sdk` 0.4.0 and has no other Python dependencies.

From a Flow Steward checkout, with `EXT` the absolute path of this repository and `ROOT` the
folder that contains it:

```bash
./core/entrypoints/cli/scripts/lint.sh --checks extension-static,extension-tests --extension-root "$EXT"
./flow-steward extensions validate flowsteward.finvalda --root "$ROOT" --health --marketplace
./flow-steward extensions test flowsteward.finvalda --root "$ROOT"
./flow-steward extensions bundle flowsteward.finvalda --root "$ROOT" --with-wheels --confirm
./flow-steward extensions install --archive dist/extensions/flowsteward.finvalda-0.1.2.zip --confirm
```

The contracts, the Coverage and Finvalda API pages and `runtime/registry.json` are generated from
the facts extracted from Finvalda's documents:

```bash
python3 tests/reference/extract_reference.py <FVS_Webservice.md> <collection.json>  # re-extract
python3 tests/reference/build_contracts.py                                           # regenerate
```

Neither source is committed — only the facts extracted from them
(`tests/reference/fvs_reference.json`), pinned by SHA-256 in `tests/reference/extract_reference.py`.
`tests/test_reference.py` re-extracts each source and compares it with the committed facts when
`FINVALDA_MANUAL` (the manual as Markdown) and `FINVALDA_POSTMAN` (the Postman collection's JSON
export) name the pinned copies.

## Support and links

- Support and issues: <https://github.com/Flow-Steward/ext-finance-finvalda/issues>
- Changes: [CHANGELOG.md](CHANGELOG.md)
- Finvalda's web service reference (Postman):
  <https://documenter.getpostman.com/view/7208231/2s8YmRMLvd>
