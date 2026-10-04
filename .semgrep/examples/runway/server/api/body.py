# Request handlers (runway/server/api/) read a body through runway/validate.py.

# ruleid: runway-unvalidated-body
flag = bool(body.get("on"))
# ruleid: runway-unvalidated-body
amount = float(body["amount"])
# ruleid: runway-unvalidated-body
count = int(body["count"])
# ruleid: runway-unvalidated-body
count = int((body or {}).get("count"))
# ruleid: runway-unvalidated-body
flag = bool(body)
# ok: runway-unvalidated-body
count = validate.integer(body, "count")
# ok: runway-unvalidated-body
page = int(q["page"][0])
