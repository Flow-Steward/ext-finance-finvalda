"""The step titles and their order in the step picker.

The picker is flat and searchable and shows no categories, so a title says what the
step does: a verb, then the object in plain English — Finvalda's own method names
stay in the descriptions. Operation ids never change (workflows use them); only the
titles and the order do.

Order: the everyday steps, then by subject — clients, products and services, prices
and discounts, stock, sales, purchases, payments, other operations, files and
documents, reference data, writes — and Export and Call at the very end.
"""

from __future__ import annotations

#: The everyday steps, in picker order, with their titles.
EVERYDAY: list[tuple[str, str]] = [
    ("test_connection", "Test the connection"),
    ("find_or_create_client", "Find or create client"),
    ("find_or_create_product", "Find or create product"),
    ("find_or_create_service", "Find or create service"),
    ("create_sales_invoice", "Create sales invoice"),
    ("create_purchase_invoice", "Create purchase invoice"),
    ("register_payment", "Register payment"),
    ("download_invoice_pdf", "Download invoice PDF"),
    ("attach_file", "Attach file"),
    ("stock_levels", "Read stock levels"),
    ("product_prices", "Read product prices"),
    ("client_debt", "Read client debt"),
    ("changes_since", "Read records changed since a date"),
]

#: Every other step: (group id, group title, group introduction, [(operation id, title)]).
GROUPS: list[tuple[str, str, str, list[tuple[str, str]]]] = [
    (
        "steps_clients",
        "Clients",
        "Clients, their addresses, groups, types and documents.",
        [
            ("descriptions_clients", "Get clients"),
            ("get_klientas", "Get a client by code"),
            ("get_klientas_e_mail", "Get a client by e-mail"),
            ("get_klientus", "Get clients changed since a date"),
            ("get_klientus_set", "Get clients (older method)"),
            ("descriptions_address", "Get client addresses"),
            ("descriptions_client_groups", "Get client groups"),
            ("descriptions_client_individual_cards", "Get client individual cards"),
            ("get_klientu_rusis_pozymius", "Get client types and tags"),
            ("get_klientus_rusies_sudeti", "Get clients of a type"),
            ("descriptions_count_clients", "Get the number of clients"),
            ("get_neapmoketi_kli_dok", "Get unpaid sales documents"),
            ("get_kliento_saskaitas", "Get a client's invoices and payments"),
            ("invoices_related_to_customer", "Get invoices related to a client"),
        ],
    ),
    (
        "steps_products",
        "Products and services",
        "Products, services, their types, groups, barcodes and units.",
        [
            ("descriptions_products", "Get products"),
            ("get_preke", "Get a product by code"),
            ("get_prekes", "Get products changed since a date"),
            ("get_prekes_set", "Get products (older method)"),
            ("get_prekes_set_ext", "Get products by type and tags"),
            ("descriptions_bar_codes", "Get product barcodes"),
            ("get_prekiu_rusis_pozymius", "Get product types and tags"),
            ("get_prekiu_rusiu_grupes", "Get product type groups"),
            ("get_prekiu_rusiu_grupes_sudeti", "Get product types in a group"),
            ("get_prekiu_rusies_sudeti", "Get products of a type"),
            ("get_prekes_istorija", "Get product history"),
            ("descriptions_partner_products", "Get partner product catalogues"),
            ("descriptions_production_item", "Get production items"),
            ("descriptions_services", "Get services"),
            ("get_paslauga", "Get a service by code"),
            ("get_paslaugos", "Get services changed since a date"),
            ("get_paslaugos_set", "Get services (older method)"),
            ("get_paslaugu_rusis_pozymius", "Get service types and tags"),
            ("get_paslaugu_rusies_sudeti", "Get services of a type"),
            ("get_matavimo_vienetus", "Get units of measure"),
        ],
    ),
    (
        "steps_prices",
        "Prices and discounts",
        "Client and client type prices and discounts by product, service or their types.",
        [
            ("descriptions_prices", "Get client prices"),
            ("descriptions_prices_by_client_type", "Get prices by client type"),
            ("descriptions_prices_by_item_type", "Get prices by item type"),
            (
                "descriptions_prices_by_client_and_item_types",
                "Get prices by client type and item type",
            ),
            ("recommended_price", "Get recommended price"),
            ("get_kli_prek_pas_nuol_pap_kain", "Get client discounts and prices by item"),
            ("get_kli_prek_pas_rus_nuol_pap_kain", "Get client discounts and prices by item type"),
            ("get_kli_rus_prek_pas_nuol_pap_kain", "Get client type discounts and prices by item"),
            (
                "get_kli_rus_prek_pas_rus_nuol_pap_kain",
                "Get client type discounts and prices by item type",
            ),
            ("get_klientu_prekiu_nuol", "Get client discounts by product"),
            ("get_klientu_prekiu_pap_kainas", "Get client prices by product"),
            ("get_klientu_prekiu_rus_nuol", "Get client discounts by product type"),
            ("get_klientu_prekiu_rus_pap_kainas", "Get client prices by product type"),
            ("get_klientu_paslaugu_nuol", "Get client discounts by service"),
            ("get_klientu_paslaugu_pap_kainas", "Get client prices by service"),
            ("get_klientu_paslaugu_rus_nuol", "Get client discounts by service type"),
            ("get_klientu_paslaugu_rus_pap_kainas", "Get client prices by service type"),
            ("get_klientu_rus_prekiu_nuol", "Get client type discounts by product"),
            ("get_klientu_rus_prekiu_pap_kainas", "Get client type prices by product"),
            ("get_klientu_rus_prekiu_rus_nuol", "Get client type discounts by product type"),
            ("get_klientu_rus_prekiu_rus_pap_kainas", "Get client type prices by product type"),
            ("get_klientu_rus_paslaugu_nuol", "Get client type discounts by service"),
            ("get_klientu_rus_paslaugu_pap_kainas", "Get client type prices by service"),
            ("get_klientu_rus_paslaugu_rus_nuol", "Get client type discounts by service type"),
            ("get_klientu_rus_paslaugu_rus_pap_kainas", "Get client type prices by service type"),
        ],
    ),
    (
        "steps_stock",
        "Stock and warehouses",
        "Stock now, by receipt or on a date, and the warehouses.",
        [
            ("descriptions_current_stock", "Get current stock"),
            ("descriptions_current_stock_det", "Get current stock by receipt"),
            ("descriptions_stock_on_date", "Get stock on a date"),
            ("get_einamieji_likuciai", "Get current stock (older method)"),
            ("get_einamieji_likuciai_ext", "Get current stock with types and tags"),
            ("get_einamieji_likuciai_ext_su_kainom", "Get current stock with prices"),
            ("get_einamieji_likuciai_grp", "Get current stock by warehouse group"),
            ("get_prekes_sandelyje", "Get products in a warehouse"),
            ("get_prekes_sandelyje_order", "Get products in a warehouse, sorted"),
            ("get_sandelius", "Get warehouses"),
            ("descriptions_warehouse_groups", "Get warehouse groups"),
        ],
    ),
    (
        "steps_sales",
        "Sales",
        "Sales, returns and reservations, and what was sold.",
        [
            ("operations_sales", "Find sales"),
            ("operations_sales_det", "Find sales lines"),
            ("operations_sales_returns", "Find sales returns"),
            ("operations_sales_returns_det", "Find sales return lines"),
            ("operations_sales_reservations", "Find sales reservations"),
            ("operations_sales_reservations_det", "Find sales reservation lines"),
            ("get_uvm_pard_r_busena", "Get order reservation status"),
            ("get_uvm_pard_r_anuliuoti", "Get cancelled order reservations"),
            ("get_uvm_pard_r_ivykdyti", "Get fulfilled order reservations"),
            ("get_uvm_pard_r_neivykdyti", "Get open order reservations"),
            ("get_pard_prek_per_perioda", "Get products sold in a period"),
            ("descriptions_count_sales", "Get sales totals"),
        ],
    ),
    (
        "steps_purchases",
        "Purchases",
        "Purchases, their returns and purchase orders.",
        [
            ("operations_purchases", "Find purchases"),
            ("operations_purchases_det", "Find purchase lines"),
            ("operations_purchases_returns", "Find purchase returns"),
            ("operations_purchases_returns_det", "Find purchase return lines"),
            ("operations_purchase_orders", "Find purchase orders"),
            ("operations_purchase_orders_det", "Find purchase order lines"),
        ],
    ),
    (
        "steps_payments",
        "Payments",
        "Incoming and outgoing payments, offsets, settlements and payment terms.",
        [
            ("operations_inflows", "Find incoming payments"),
            ("operations_inflows_det", "Find incoming payment lines"),
            ("operations_disbursement", "Find outgoing payments"),
            ("operations_disbursement_det", "Find outgoing payment lines"),
            ("operations_clearing_off", "Find offsets"),
            ("operations_clearing_off_det", "Find offset lines"),
            ("get_atsiskaitymai_uz_dok", "Get settlements of a document"),
            ("get_atsiskaitymai_uz_dok_det", "Get settlement lines of a document"),
            (
                "get_atsiskaitymai_uz_dok_data_nuo_det",
                "Get settlement lines of a document since a date",
            ),
            ("get_atsiskaitymai_uz_dok_data_nuo_det_param", "Get settlement lines by parameters"),
            ("get_atsiskaitymo_term", "Get payment terms"),
        ],
    ),
    (
        "steps_other_operations",
        "Other operations",
        "Transfers, receipts, write-offs, production and other operations.",
        [
            ("operations_internal_transactions", "Find internal transfers"),
            ("operations_internal_transactions_det", "Find internal transfer lines"),
            ("operations_capitalization", "Find receipts"),
            ("operations_capitalization_det", "Find receipt lines"),
            ("operations_write_off", "Find write-offs"),
            ("operations_write_off_det", "Find write-off lines"),
            ("operations_production", "Find production operations"),
            ("operations_gen_leadger", "Find other non-analytical operations"),
            ("operations_gen_leadger_det", "Find other non-analytical operation lines"),
            ("get_veikla_pagal_objektus", "Get activity by objects"),
            ("is_operation_locked", "Check if an operation is locked"),
        ],
    ),
    (
        "steps_files",
        "Files and documents",
        "Invoice and report PDFs, product images and Finvalda's document register.",
        [
            ("make_invoice", "Print an invoice form to PDF"),
            ("descriptions_invoice_list", "Get invoice forms"),
            ("make_report", "Download report PDF"),
            ("descriptions_report_list", "Get reports"),
            ("auto_reports_list", "Get automatic reports"),
            ("auto_report", "Download an automatic report"),
            ("product_image", "Download product image"),
            ("insert_document", "Store a document"),
            ("attach_document", "Attach a document"),
            ("attached_documents", "Get attached documents"),
            ("delete_document", "Delete a document"),
        ],
    ),
    (
        "steps_reference",
        "Reference data",
        "Accounts, currencies, series, journals, statuses, taxes, objects and permissions.",
        [
            ("descriptions_accounts", "Get chart of accounts"),
            ("descriptions_currency_rates", "Get currency rates"),
            ("descriptions_document_series", "Get document series"),
            ("descriptions_logbook_groups", "Get journal groups"),
            ("descriptions_op_type_groups", "Get operation type groups"),
            ("descriptions_operation_statuses", "Get operation statuses"),
            ("descriptions_types_and_tags", "Get types and tags"),
            ("get_mokesciai", "Get taxes"),
            ("descriptions_fixed_asset", "Get fixed assets"),
            ("descriptions_vehicles", "Get vehicles"),
            ("descriptions_calendar_events", "Get calendar events"),
            ("descriptions_company_work_start", "Get company start date"),
            ("get_objektai1_set", "Get level 1 objects"),
            ("get_objektai2_set", "Get level 2 objects"),
            ("get_objektai3_set", "Get level 3 objects"),
            ("get_objektai4_set", "Get level 4 objects"),
            ("get_objektai5_set", "Get level 5 objects"),
            ("get_objektai6_set", "Get level 6 objects"),
            ("get_objektas1", "Get a level 1 object by code"),
            ("get_objektas2", "Get a level 2 object by code"),
            ("get_objektas3", "Get a level 3 object by code"),
            ("get_objektas4", "Get a level 4 object by code"),
            ("get_objektas5", "Get a level 5 object by code"),
            ("get_objektas6", "Get a level 6 object by code"),
            ("user_permissions", "Get employee permissions"),
        ],
    ),
    (
        "steps_records",
        "Records",
        "Creating, changing and deleting clients, products, services and other records.",
        [
            ("insert_item", "Create a record"),
            ("edit_item", "Update a record"),
            ("edit_item_props", "Update many records"),
            ("append_group", "Add a record to a group"),
            ("delete_item", "Delete a record"),
        ],
    ),
    (
        "steps_operations",
        "Operations",
        "Creating, changing, deleting, locking, moving and copying operations.",
        [
            ("insert_operation", "Create an operation"),
            ("update_operation", "Change an operation"),
            ("delete_operation", "Delete an operation"),
            ("lock_operation", "Lock an operation"),
            ("unlock_operation", "Unlock an operation"),
            ("change_journal", "Move an operation to another journal"),
            ("copy_operation", "Copy an operation"),
        ],
    ),
    (
        "steps_any",
        "Any list or method",
        "Any list as a file, and any documented method by name.",
        [
            ("export_list", "Export a list to a file"),
            ("call_method", "Call a Finvalda method"),
        ],
    ),
]

#: The verbs a title may start with.
VERBS = (
    "Add",
    "Attach",
    "Call",
    "Change",
    "Check",
    "Copy",
    "Create",
    "Delete",
    "Download",
    "Export",
    "Find",
    "Get",
    "Lock",
    "Move",
    "Print",
    "Read",
    "Register",
    "Store",
    "Test",
    "Unlock",
    "Update",
)


def titles() -> dict[str, str]:
    """Every operation id with its title."""
    out = dict(EVERYDAY)
    for _gid, _title, _intro, steps in GROUPS:
        out.update(steps)
    return out


def order() -> list[str]:
    """Every operation id in picker order."""
    return [oid for oid, _ in EVERYDAY] + [
        oid for _gid, _title, _intro, steps in GROUPS for oid, _ in steps
    ]


__all__ = ["EVERYDAY", "GROUPS", "VERBS", "order", "titles"]
