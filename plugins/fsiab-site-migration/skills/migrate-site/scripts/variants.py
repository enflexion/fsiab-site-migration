"""Collapse multi-option variants into FSIAB price tiers (max 8, one label + price each).

tiers_from_variants(options, variants) -> (tiers, notes)
  options:  ["Size", "Colour", ...]                       option names in order
  variants: [{"values": ["Small", "Pink"], "price": "69.00", "sku": "", "in_stock": True}, ...]

1. An option that never changes the price (for every combination of the other options, all its
   values cost the same) is dropped from the tiers and reported as a choice.
2. If more than 8 tiers remain, drop the option whose removal causes the fewest price conflicts,
   take the lowest price in each merged group, and report it. Repeat until 8 or fewer.
"""
from collections import defaultdict


def _neutral(opt_i, keep, variants):
    groups = defaultdict(set)
    for v in variants:
        key = tuple(v["values"][j] for j in keep if j != opt_i)
        groups[key].add(v["price"])
    return all(len(p) == 1 for p in groups.values())


def _conflicts(drop_i, keep, variants):
    groups = defaultdict(set)
    for v in variants:
        groups[tuple(v["values"][j] for j in keep if j != drop_i)].add(v["price"])
    return sum(1 for p in groups.values() if len(p) > 1)


def _values(opt_i, variants):
    seen = []
    for v in variants:
        x = v["values"][opt_i]
        if x not in seen:
            seen.append(x)
    return seen


def tiers_from_variants(options, variants):
    notes = []
    if not variants:
        return [], notes
    if len(variants) == 1 or not options:
        v = variants[0]
        return [{"label": None if len(variants) == 1 else " / ".join(v["values"]), "price": v["price"], "sku": v.get("sku", ""), "in_stock": v.get("in_stock", True)}], notes
    keep = list(range(len(options)))

    def build(keep):
        groups = {}
        for v in variants:
            key = tuple(v["values"][j] for j in keep)
            g = groups.setdefault(key, {"prices": [], "skus": [], "in_stock": False})
            g["prices"].append(float(v["price"]))
            if v.get("sku"):
                g["skus"].append(v["sku"])
            g["in_stock"] = g["in_stock"] or v.get("in_stock", True)
        return groups

    groups = build(keep)
    # 1. fits: every combination stays a tier, so the customer keeps their choice
    # 2. too many: drop options that never change the price, one at a time
    while len(groups) > 8 and len(keep) > 1:
        neutral = [i for i in keep if _neutral(i, keep, variants) and len(_values(i, variants)) > 1]
        if not neutral:
            break
        i = neutral[0]
        keep.remove(i)
        notes.append(f"'{options[i]}' does not change the price and there were too many combinations for 8 price tiers, "
                     "so it is not a price option on FSIAB. Choices were: " + ", ".join(_values(i, variants)))
        groups = build(keep)
    # 3. still too many: merge the option that causes the fewest price conflicts
    while len(groups) > 8 and len(keep) > 1:
        best = min(keep, key=lambda i: _conflicts(i, keep, variants))
        c = _conflicts(best, keep, variants)
        keep.remove(best)
        notes.append(f"too many combinations for 8 price tiers, so '{options[best]}' was merged"
                     + (f"; {c} group(s) had different prices and the lowest was used. CHECK THIS PRODUCT" if c else "")
                     + ". Choices were: " + ", ".join(_values(best, variants)))
        groups = build(keep)
    # 4. one option left, more than 8 values, all the same price: one tier, list the choices
    if len(groups) > 8 and len(keep) == 1 and len({min(g["prices"]) for g in groups.values()}) == 1:
        notes.append(f"'{options[keep[0]]}' has {len(groups)} choices at one price; FSIAB holds 8 tiers, so it is one price. "
                     "Choices were: " + ", ".join(k[0] for k in groups))
        keep = []
        groups = build(keep)
    tiers = []
    for key, g in groups.items():
        lo, hi = min(g["prices"]), max(g["prices"])
        if hi != lo:
            notes.append(f"'{' / '.join(key)}' had prices from ${lo:.2f} to ${hi:.2f}; ${lo:.2f} used")
        tiers.append({"label": " / ".join(key) if key else None, "price": f"{lo:.2f}",
                      "sku": g["skus"][0] if len(set(g["skus"])) == 1 else "", "in_stock": g["in_stock"]})
    if len(tiers) == 1 and not keep:
        tiers[0]["label"] = None
    return tiers, notes
