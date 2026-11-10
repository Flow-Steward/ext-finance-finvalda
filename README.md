# Finvalda for Flow Steward

`flowsteward.finvalda` connects Flow Steward workflows to **Finvalda**, the Lithuanian
accounting and business management system, through its web service — the REST interface
`FvsServicePure` that Finvalda installs on the company's server.

Every step follows Finvalda's own documentation:

- the manual "FVS Webserviso metodų naudojimo instrukcija", revision 110 (2025-04-14), which
  Finvalda gives to its customers;
- Finvalda's published Postman collection "Finvaldos webservisas V2"
  ([documenter.getpostman.com](https://documenter.getpostman.com/view/7208231/2s8YmRMLvd)).

## Steps

Everyday steps (listed first in the step picker): Find or create client, Find or create product,
Find or create service, Create sales invoice, Create purchase invoice, Register payment, Download
invoice PDF, Attach file, Stock levels, Product prices, Client debt, Changes since.

Every documented method also has its own step ("Advanced · …"):

- **Lists** — one step per GetDescriptions type (clients, products, services, stock, stock on a
  date, prices, barcodes, addresses, fixed assets, currency rates, series, reports, invoice forms
  and the rest), with the documented filters, columns and pages.
- **Operations** — one step per GetOperations class (sales, purchases, returns, inflows,
  disbursements, offsets, reservations, orders, transfers, write-offs, receipts, production and
  their lines).
- **Older read methods** — one step per method of the manual's section 3 (GetKlientusSet,
  GetEinamiejiLikuciai, GetNeapmoketiKliDok, the discount and price tables, objects, order
  management and the rest).
- **Writes** — create, update or delete a record (InsertNewItem, EditItem, EditItemProps,
  DeleteItem, AppendGroup); create, change or delete an operation (InsertNewOperation,
  UpdateOperation, DeleteOperation); lock, unlock, move to another journal, copy.
- **Files** — invoice and report PDFs (MakeInvoice, MakeReport, automatic reports), product
  pictures, the document register (InsertDocument, AttachDocument, GetAttachedDocuments,
  DeleteDocument).
- **Other** — recommended prices, employee permissions, an export of any list to a JSON Lines
  file, and a raw call of any documented method.

The **Coverage** page lists every name of both sources and the step that offers it.
`tests/reference/coverage.json` is the same map in machine-readable form.

## Connecting

The company's Finvalda administrator makes the web service reachable over https (for example
`https://fvs.example.lt/FvsServicePure.svc`), creates a Finvalda user for it, and prepares the
import parameters in Finvalda's FvsNETParamKonfig tool: a preset for operations (journal,
operation type, series, accounts) and one for new records (accounts, VAT, unit). The connection
holds the address, the user and its password, the company code (CompanyID) when the service
serves several companies, and the two import parameters.

## Writes and failures

- Every write is a declared external effect, suppressed in test runs.
- Fields of records and operations use the manual's names and are checked against its field
  tables (name, kind, length, required) before anything is sent.
- When Finvalda refuses a change it answers `AccessResult: Fail` with a result code (`nResult`);
  nothing was saved. The step passes the code on with the manual's meaning (appendix I).
- The codes the manual describes as program, memory, recordset or transaction failures (2001,
  2002, 2010, 2031), server errors (the WCF interface answers an exception with HTTP 400 or 500),
  timeouts and lost connections leave a write's outcome unknown (`timeout_unknown`). Nothing
  ambiguous is retried.
- Everyday steps look up before they create: clients by code, company code, VAT code and e-mail;
  products by code and barcode; services by code; invoices and payments by their document number
  for that client. Finvalda itself refuses a document number already used (codes 1001, 1035,
  1058). A step that saves more than one thing (an invoice and its file) names what it already
  saved when a later call fails.
- The password travels only in the request header the web service requires; it is removed from
  every answer before it is read, and never appears in a result or an error.
- The web service address must be https and a public host name; the SDK's pinned opener refuses
  private and reserved addresses.

## Verification

No Finvalda server was available while version 0.1.0 was built. The steps follow the manual and
the Postman collection and are tested against recorded answers from them; they have not been run
against a live Finvalda. Where a published client of the same web service reports a parameter
name that differs from the manual (`sSandKod` for `sSanKod`; `tDataNuo`/`tDataIki` for
`tNuo`/`tIki`), the step sends both names.

## Development

From the extension folder:

```bash
python3 tests/reference/extract_reference.py <FVS_Webservice.md>   # re-extract the reference
python3 tests/reference/build_contracts.py                         # regenerate contracts and pages
```

From the Flow Steward checkout (all checks run in the `fs-audit` container):

```bash
EXT=$PWD/workspace/extensions/flowsteward_finvalda
./core/entrypoints/cli/scripts/lint.sh --checks extension-static,extension-tests --extension-root "$EXT"
./flow-steward extensions validate flowsteward.finvalda --root <bundle folder> --health --marketplace
```

`tests/test_reference.py` re-extracts the manual and compares it with the committed reference when
`FINVALDA_MANUAL` names the pinned copy (SHA-256 in `tests/reference/extract_reference.py`).
