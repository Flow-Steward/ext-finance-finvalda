# Changelog

## 0.1.3 — 2026-11-13

- Every step is named by what it does — a verb, then the object in plain English: "Get currency
  rates", "Find sales lines", "Get a client by code", "Create an operation", "Print an invoice form
  to PDF". The "Advanced · …", "List · …" and "Operations · …" prefixes are gone; Finvalda's method
  names stay in the descriptions. Steps that read the same thing differently say how ("Get clients
  changed since a date"; older duplicates end in "(older method)"). No two titles are alike.
- The step picker lists the everyday steps first, then the others by subject: clients, products and
  services, prices and discounts, stock, sales, purchases, payments, other operations, files and
  documents, reference data, records, operations, and Export and Call at the end. The Finvalda API
  page follows the same order. Step ids are unchanged, so existing workflows keep working.
- A lookup answer for a client that shows only other clients is an error, as for products and
  services; nothing is created.
- A record created but not found when read back is said so in the warnings.
- A result code that cannot be read as a number counts as a failure; the next code field is read
  when one is not a number.
- Amounts beyond 10^40 are kept exactly (in scientific form) and rounded to cents safely; very
  small or very large input numbers get a clear message.
- Attached files: their size is read the same way as their content, and a file too large is
  refused before it is decoded.

## 0.1.2

- Clarify everyday read action titles and add schema-compatible form examples.
- Add a workflow action guide covering setup, invoice lines, paging and advanced inputs.


## 0.1.1

### Changed

- Clarified the catalog description, supported operations and connection setup.

### Security

- Updated the CI pytest pin from 8.3.5 to 9.0.3.

## 0.1.0 — 2026-11-10

First release.

- Finvalda's web service (the FvsServicePure interface) with the web service user, password and
  company of the connection.
- Everyday steps: Test the connection, Find or create client, Find or create product, Find or create service, Create
  sales invoice, Create purchase invoice, Register payment, Download invoice PDF, Attach file, Stock
  levels, Product prices, Client debt, Changes since.
- Every documented method as its own step: every list of GetDescriptions, every operation class of
  GetOperations, every older read method, record and operation writes (create, change, delete, lock,
  copy, move), invoice and report PDFs, documents and attachments, recommended prices and employee
  permissions. A coverage page names the source of each and the few names without a step of their
  own, with the reason.
- Fields of records and operations are checked against the manual before anything is sent.
- Every write is a declared external effect, suppressed in test runs; Finvalda's refusals come
  with their code and meaning; ambiguous outcomes are reported as unknown and not retried; everyday
  steps look up before they create.
- Built from Finvalda's manual (revision 110) and Postman collection; not yet verified against a
  live Finvalda server.
- Icon: Finvalda's own FVS mark from finvalda.lt
  ([android-icon-192x192.png](https://finvalda.lt/wp-content/themes/finvalda/assets/fav/android-icon-192x192.png)),
  scaled to 128×128, used to identify the integration.
