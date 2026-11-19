# Finvalda integration for Flow Steward

<img src="icon.png" width="96" alt="Finvalda integration for Flow Steward">

[![CI](https://img.shields.io/github/actions/workflow/status/Flow-Steward/ext-finance-finvalda/ci.yml?branch=main&label=CI)](https://github.com/Flow-Steward/ext-finance-finvalda/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/Flow-Steward/ext-finance-finvalda?label=release)](https://github.com/Flow-Steward/ext-finance-finvalda/releases/latest)

Automate Finvalda accounting with Flow Steward workflows and AI agents: create invoices, register payments, read stock and debts — without writing code.

This Flow Steward extension is a Finvalda integration for accounting automation in Lithuania. It works through the Finvalda web service API (FvsServicePure) and brings workflow automation to clients, products, sales and purchase invoices, payments, stock, prices and documents.

## What is this

Finvalda is an accounting and business management system used by companies in Lithuania for bookkeeping, sales, purchases and warehouse. This extension is a connector: it adds ready Finvalda steps to the Flow Steward workflow editor and the same operations as tools for AI agents. Read more about Flow Steward at <https://docs.flowsteward.org>.

## What you can automate

- Create sales invoices in Finvalda from orders in your online shop — the client and products are found or created first, no retyping.
- Book supplier invoices as purchase invoices and attach the original PDF to the document.
- Register payments from a bank statement against the invoices they pay.
- Send current stock by warehouse and sales prices from Finvalda to your shop or marketplace.
- Check a client's debt and overdue documents before accepting a new order.
- Send an invoice PDF from Finvalda to the client by e-mail or to your archive.
- Pass clients, products, sales, purchases and payments changed since a date to another system.

## Why teams use it

- **No duplicate documents.** Everyday steps look up before they create: clients, products and services by code, invoices and payments by document number for that client. A rerun of an invoice returns the one already created; when the match is unclear, the step stops and writes nothing.
- **Amounts without rounding errors.** Money is handled as exact decimals from input to Finvalda and back; an amount with more than cents is refused rather than rounded.
- **A change is not sent twice.** When Finvalda's answer does not confirm a write, the step reports the outcome as unknown instead of retrying, so the workflow can check Finvalda first.
- **A test run changes nothing.** Writes are declared external effects, and Flow Steward suppresses them in test runs, so you can try a workflow without touching the books.
- **The whole documented API is covered.** 173 steps: 13 everyday steps plus a step for every documented method of the web service.
- **The password stays secret.** It travels only in the header the web service requires and is removed from every answer before the answer is read.

## What's inside

Everyday steps:

- **Test the connection** — checks the address, user, password and company in one click.
- **Find or create client** — finds a client by code, company code, VAT code or e-mail, creates it when missing.
- **Find or create product** — finds a product by code or barcode, creates it when missing.
- **Find or create service** — finds a service by code, creates it when missing.
- **Create sales invoice** — product and service lines with VAT computed, optionally with a PDF attached.
- **Create purchase invoice** — the same for a supplier's invoice.
- **Register payment** — money received from a client or paid to a supplier, against an invoice or as a prepayment.
- **Download invoice PDF** — the invoice form as a workflow file, ready for e-mail or archive.
- **Attach file** — attaches a workflow file to an operation, a client or a product.
- **Read stock levels** — quantity, quantity with reservations and cost by warehouse.
- **Read product prices** — the sales prices, the purchase price and a client's special prices.
- **Read client debt** — debt and unpaid documents, with the overdue part.
- **Read records changed since a date** — clients, products, services, sales, purchases or payments, page by page.

Beyond these, 160 more API operations are available as steps: clients, products and services, prices and discounts, stock and warehouses, sales, purchases, payments and settlements, reports and invoice forms, documents and files, reference data, records and operations, and list exports. The [workflow action guide](docs/workflow-actions.md) shows examples.

## Get started

1. In Flow Steward, open **Extensions → Catalog**, find **Finvalda** and install it.
2. Open the extension's **Connection** page and add a connection: the web service address (https), the web service user and password, the company code if the service serves several companies, the import parameter for operations, the import parameter for records and the Finvalda employee.
3. Press **Test connection**. It reads one client to check everything.
4. Build your first workflow: for example, an order arrives → **Find or create client** → **Create sales invoice** → **Download invoice PDF** → e-mail.

## Requirements

- Finvalda with the web service module (FvsServicePure), an add-on of Finvalda MINI, MIDI and MAXI, reachable over https on a public host name with a valid certificate.
- A Finvalda user for the web service with the rights your workflows need: the steps can do exactly what that user can do.
- A Finvalda employee for documents and files (an employee, not the web service user).
- Import parameters prepared in Finvalda's FvsNETParamKonfig tool: one for operations (journal, operation type, series, accounts) and one for new records (accounts, VAT, unit). Without an operation parameter Finvalda accepts no invoice or payment.
- Flow Steward 0.9.16 or later.

## FAQ

**How do I create invoices in Finvalda from an online shop?**
Add a trigger for new orders, then **Find or create client**, **Find or create product** for each line and **Create sales invoice** with the order number as the document number.

**Can Flow Steward send Finvalda stock and prices to my e-shop?**
Yes. **Read stock levels** and **Read product prices** return stock by warehouse and prices that the next steps pass to your shop.

**Will running a workflow twice create a duplicate invoice in Finvalda?**
No. A rerun with the same client and document number returns the existing invoice. For payments, an existing payment with the same document number for that client stops the step with a conflict, so give each payment its own number.

**Can one Flow Steward project work with several Finvalda companies?**
Yes. Each company is its own connection, and each step picks the connection to use.

**What does the Finvalda administrator need to set up?**
Enable the web service on an https address, create a web service user, name the Finvalda employee and prepare the two import parameters. The extension's **Setup Guide** page walks through each of these.

## In your language

Finvalda integracija su Flow Steward padeda automatizuoti apskaitą be rankinio darbo. Plėtinys per Finvalda webservisą kuria klientus, prekes, pardavimo ir pirkimo sąskaitas faktūras, registruoja mokėjimus ir skaito likučius, kainas bei skolas. Tinka įmonėms Lietuvoje, kurios nori sujungti Finvalda su el. parduotuve ar kita sistema.

## Links

- Flow Steward documentation: <https://docs.flowsteward.org>
- Flow Steward extension catalog: <https://github.com/Flow-Steward/extensions>
- Issues and support: <https://github.com/Flow-Steward/ext-finance-finvalda/issues>
- Finvalda web service API reference (Postman): <https://documenter.getpostman.com/view/7208231/2s8YmRMLvd>
- Changes: [CHANGELOG.md](CHANGELOG.md)

## For developers

The extension is built on `flowsteward-extension-sdk` 0.4.0 and has no other Python dependencies. The contracts, the Coverage and Finvalda API pages and `runtime/registry.json` are generated from facts extracted from Finvalda's documents (`tests/reference/fvs_reference.json`):

```bash
python3 tests/reference/extract_reference.py <FVS_Webservice.md> <collection.json>  # re-extract
python3 tests/reference/build_contracts.py                                           # regenerate
```

From a Flow Steward checkout, with `EXT` the absolute path of this repository and `ROOT` the folder that contains it (the checks run in the `fs-audit` container):

```bash
./core/entrypoints/cli/scripts/lint.sh --checks extension-static,extension-tests --extension-root "$EXT"
./flow-steward extensions validate flowsteward.finvalda --root "$ROOT" --health --marketplace
./flow-steward extensions test flowsteward.finvalda --root "$ROOT"
```

Finvalda and its marks belong to their owner; see [BRANDING.md](BRANDING.md) for the icon's source.
