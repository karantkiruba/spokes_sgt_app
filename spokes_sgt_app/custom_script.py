import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.query_builder.functions import Sum
from frappe.utils import flt, getdate
from erpnext.stock.doctype.item.item import get_item_defaults
import erpnext
import json
from datetime import date,datetime,timedelta
from frappe import ValidationError, _, qb, scrub, throw
from frappe.utils import cint, comma_or, flt, getdate, nowdate
from frappe.utils.data import comma_and, fmt_money
from pypika import Case
from pypika.functions import Coalesce, Sum
from erpnext.controllers.accounts_controller import (
        AccountsController,
        get_supplier_block_status,
        validate_taxes_and_charges,
)
from erpnext.stock.utils import get_latest_stock_qty
from erpnext.controllers.taxes_and_totals import get_itemised_tax_breakup_data
from collections import defaultdict
from erpnext.setup.doctype.item_group.item_group import get_item_group_defaults
from frappe.model.utils import get_fetch_values
from erpnext.accounts.party import get_party_account
from erpnext.accounts.utils import (
	get_account_currency,
	get_balance_on,
	get_outstanding_invoices,
)


@frappe.whitelist()
def add_tax_breakup(self, method):
	breakup_dtl = []
	if self.taxes:
		tax_breakup = get_itemised_tax_breakup_data(self)
		self.sales_taxes_other_calculations = []
		if tax_breakup:
			for item_code, taxes_info in tax_breakup[0].items():
				taxable_amount = tax_breakup[1].get(item_code, 0.0)
				for tax_type, tax_details in taxes_info.items():
					tax_rate = tax_details.get('tax_rate', 0.0)
					tax_amount = tax_details.get('tax_amount', 0.0)
					output_tax_cgst = 0.0
					if tax_type == 'Output Tax CGST':
						output_tax_cgst = tax_amount
					elif tax_type == 'Output Tax SGST':
						output_tax_cgst = tax_amount
					elif tax_type == 'Output Tax IGST':
						output_tax_cgst = tax_amount
					elif tax_type == 'Input Tax CGST':
						output_tax_cgst = tax_amount
					elif tax_type == 'Input Tax SGST':
						output_tax_cgst = tax_amount
					elif tax_type == 'Input Tax IGST':
						output_tax_cgst = tax_amount					
					cost_center = f"{tax_type.replace('Output Tax ', '')}-{int(tax_rate)}"
					breakup_dtl.append({
						'hsn': item_code,
						'taxable_amount': round(float(taxable_amount), 2),
						'tax_rate': tax_rate,
						'output_tax_cgst': round(float(output_tax_cgst), 2),
						'tax_cgst': cost_center
					})
	tax_dtl = []
	grouped_data = defaultdict(lambda: {"output_tax_cgst": 0.0, "taxable_amount": 0.0, "tax_rate": 0.0})
	for item in breakup_dtl:
		tax_type = item["tax_cgst"]
		grouped_data[tax_type]["output_tax_cgst"] += item.get("output_tax_cgst")
		grouped_data[tax_type]["taxable_amount"] += item["taxable_amount"]
		grouped_data[tax_type]["tax_rate"] = item["tax_rate"]
	result = [{"tax_cgst": key, **values} for key, values in grouped_data.items()]
	for x in result:
		self.append('sales_taxes_other_calculations', x)
	frappe.db.commit()


@frappe.whitelist()
def trigger_global_alert(title,message):
	active_users = frappe.get_all('User',
        filters={'enabled': 1},
        fields=['name']
	)

	alert_data = {
        	"title": title,
        	"message": message
	}

	for user in active_users:
		frappe.publish_realtime(
           	 event='msgprint',
           	 message=alert_data,
            	user=user.name
		)


def deliverychallansubmit(self,method):
	stockentryitem = []
	stockentry =frappe.get_doc({
                    "doctype": "Stock Entry",
                    "stock_entry_type" :"Material Transfer",
                    "delivery_challan": self.name,
                    "company":self.company,
	})
	for dtl in self.items:
		if dtl.is_stock: 
			stockentryitem.append({"s_warehouse": self.source_warehouse,
			"t_warehouse" :self.target_warehouse,
                	"item_code" : dtl.item_code,
                	"qty" :dtl.qty,
                	"allow_zero_valuation_rate":1
                	})
	if stockentryitem:
		stockentry.set("items",stockentryitem)
		stockentry.save()
		stockentry.submit()

	
def updatedcinwqty(self,method):
	if self.delivery_challan:

		stockreconcilitem =[]
		qty_after_transaction=0.0
		valuation_rate =0.0
		amount =0.0
		total_diff=0.0
		for dtl in self.items:
			if dtl.is_stock:
				qty_after_transaction = get_stock_balance(dtl.item_code, self.supplier_warehouse, self.posting_date, self.posting_time)
				valuation_rate = get_stock_balance(dtl.item_code, self.supplier_warehouse, self.posting_date, self.posting_time)
				stockreconcilitem.append({"item_code" : dtl.item_code,
						  "warehouse" :self.supplier_warehouse,
                                                  "qty" :qty_after_transaction - dtl.qty ,
                                                  "valuation_rate" : 1,
                                                  "amount": flt(qty_after_transaction - dtl.qty) * flt(1),
                                                  "quantity_difference" : flt(qty_after_transaction - dtl.qty ) - flt(qty_after_transaction),
                                                  "current_qty" : qty_after_transaction,
                                                  "amount_differnce" : flt(qty_after_transaction - dtl.qty) * flt(1)
				})
				total_diff +=flt(qty_after_transaction - dtl.qty) * flt(1)
		if stockreconcilitem:
			stockreconcil =frappe.get_doc({
                                       "doctype": "Stock Reconciliation",
                                       "company": self.company,
                                       "purpose" :'Stock Reconciliation',
                                       "cost_center" :'Main - SGTPL',
                                       "expense_account" : 'Stock Adjustment - SGTPL',
                                       "difference_amount": total_diff,
				       "purchase_receipt": self.name
			})
			stockreconcil.set("items",stockreconcilitem)
			stockreconcil.save()
			stockreconcil.submit()
		for dtl in self.items:
			inwardqty =frappe.get_doc("Delivery Challan Item",dtl.delivery_challan_item);
			frappe.db.set_value('Delivery Challan Item',dtl.delivery_challan_item, 'inward_qty', float(inwardqty.inward_qty) + dtl.qty)

def cancelpurchasereceipt(self,method):
	if self.delivery_challan:
		for dtl in self.items:
			inwardqty =frappe.get_doc("Delivery Challan Item",dtl.delivery_challan_item);
			frappe.db.set_value('Delivery Challan Item',dtl.delivery_challan_item, 'inward_qty', float(inwardqty.inward_qty) - dtl.qty)

@frappe.whitelist()
def deliverychallan_query(doctype, txt, searchfield, start, page_len, filters):
        return frappe.db.sql("""SELECT DISTINCT cs.name,supp.supplier_name
        FROM  `tabDelivery Challan` cs join `tabDelivery Challan Item` dtl on dtl.parent = cs.name
        join   `tabSupplier` supp on supp.name = cs.supplier
        WHERE   ifnull(dtl.qty,0) > ifnull((select sum(ifnull(prdtl.qty,0)) from `tabPurchase Receipt Item` prdtl join `tabPurchase Receipt` hdr on hdr.name=prdtl.parent
                where prdtl.delivery_challan_item = dtl.name and hdr.delivery_challan NOT LIKE '%%%%'),0) and cs.docstatus =1 and
                (cs.{key} LIKE %(txt)s or supp.supplier_name like %(txt)s)
        """.format(
        key = searchfield,
        #fcond=get_filters_cond(doctype, filters, conditions).replace('%', '%%'),
        #mcond=get_match_cond(doctype).replace('%', '%%')
        #month= filters.get('schedulemonth'),
        #year= filters.get('scheduleyear')
        ),{
        'txt': "%%%s%%" % txt,
        'start': start,
        'page_len': page_len
    })


import frappe
import requests
from erpnext.accounts.utils import get_fiscal_year

@frappe.whitelist()
def update_einvoice_portal():

    config = frappe.get_doc(
        "Site API Config",
        "Site API Config"
    )

    fy = get_fiscal_year()
    fiscal_year = fy["name"]
    portal_name = frappe.db.get_value(
        "E-Invoice Portal",
        {
            "fiscal_year": fiscal_year
        },
        "name"
    )

    if portal_name:
        doc = frappe.get_doc(
            "E-Invoice Portal",
            portal_name
        )
    else:
        doc = frappe.new_doc(
            "E-Invoice Portal"
        )

        doc.fiscal_year = fiscal_year

    for site in config.table_sites:
        if not site.enabled:
            continue
        try:
            method = (
                f"{site.app_name}"
                ".custom_script.get_portal_count"
            )
            url = (
                f"https://{site.site}"
                f"/api/method/{method}"
            )
            response = requests.get(
                url,
                timeout=20
            )
            result = response.json()

            data = result.get("message")
            if not data:
                frappe.log_error(
                    str(result),
                    f"Invalid API Response - {site.site}"
                )
                continue

            row_exists = False
            for row in doc.table_uohc:
                if row.site_name == data["site"]:
                    row.count = data["count"]

                    row_exists = True

                    break
            if not row_exists:
                doc.append(
                    "table_uohc",
                    {
                        "site_name": data["site"],
                        "count": data["count"]
                    }
                )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Portal Sync Failed - {site.site}"
            )
    if doc.is_new():
        doc.insert(
            ignore_permissions=True
        )
    else:
        doc.save(
            ignore_permissions=True
        )
    frappe.db.commit()


