from sample_app.inventory import inventory, total_price


def build_report(path, id):
    inv = inventory(path)
    total = total_price(inv.items)
    if total == None:
        return
    result = "Report %s: total=%s" % (id, total)
    unused = 42
    return result
