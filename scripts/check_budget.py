"""Print this cycle's spend across EVERY meter: python scripts/check_budget.py

Claude in dollars; each vendor in its native units (the enforcement currency) with
the USD overlay (est_usd_per_unit) folded into one cross-vendor total at the end —
"no surprise bill" as a single number.
"""
from core import cost_guard

if __name__ == "__main__":
    spent = cost_guard.month_to_date_spend()
    cap = cost_guard.ceiling()
    print(f"claude    ${spent:.2f} / ${cap:.2f}   (remaining ${cap - spent:.2f})")
    total_est = spent
    for vendor in cost_guard.metered_vendors():
        used = cost_guard.vendor_usage(vendor)
        vcap = cost_guard.vendor_cap(vendor)
        rate = cost_guard.vendor_usd_rate(vendor)
        est = used * rate
        total_est += est
        priced = f"  ≈ ${est:.2f}" if rate else "   (est_usd_per_unit not set)"
        print(f"{vendor:<9} {used:g} / {vcap:g} units{priced}")
    print(f"{'TOTAL':<9} ≈ ${total_est:.2f} this cycle (estimate)")
