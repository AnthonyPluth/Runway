# Examples for .semgrep/runway.yml. Each `# ruleid: <id>` comment says the next line must be flagged by that rule, each
# `# ok: <id>` that it must not be; `make semgrep-test` (.semgrep/check_examples.py) checks both, and that nothing else
# is flagged. Nothing here runs; it is only parsed. The path matters: a rule only looks where its `paths` say, so the
# files beside this one (server/api/body.py, server/sync.py, tls.py, money.py, dates.py, migrations/) cover those.
import calendar
import datetime
import urllib.request
from datetime import date
from datetime import datetime as dt

import sqlalchemy as sa
from dateutil.relativedelta import relativedelta
from sqlalchemy import text

# ruleid: runway-raw-urlopen
urllib.request.urlopen(req, timeout=5)
# ruleid: runway-raw-urlopen
urllib.request.build_opener()
# ok: runway-raw-urlopen
tls.urlopen(req, timeout=5)

# ruleid: runway-sql-from-string
sa.text(f"SELECT * FROM {name}")
# ruleid: runway-sql-from-string
text("SELECT * FROM " + name)
# ruleid: runway-sql-from-string
text("SELECT * FROM %s" % name)
# ruleid: runway-sql-from-string
text("SELECT * FROM {}".format(name))
# ruleid: runway-sql-from-string
conn.exec_driver_sql(f"SELECT * FROM {name}")
# ruleid: runway-sql-from-string
conn.execute(f"SELECT * FROM {name}")
# ok: runway-sql-from-string
text("SELECT * FROM accounts WHERE id = :id")
# ok: runway-sql-from-string
conn.execute(text("SELECT * FROM accounts WHERE id = :id"), {"id": 1})

# ruleid: runway-half-cent-literal
small = abs(total) < 0.005
# ruleid: runway-half-cent-literal
CENT = 0.005
# ok: runway-half-cent-literal
small = abs(total) < money.CENT
# ok: runway-half-cent-literal
price = 0.0051

# ok: runway-today-in-sync
start = date.today()   # only sync and provider modules (see server/sync.py)

# ruleid: runway-utcnow
stamp = dt.utcnow()
# ruleid: runway-utcnow
stamp = datetime.datetime.utcnow()
# ok: runway-utcnow
stamp = datetime.datetime.now(datetime.timezone.utc)

# ruleid: runway-month-arithmetic
later = today + relativedelta(months=1)
# ruleid: runway-month-arithmetic
later = today + relativedelta(years=1, months=2)
# ruleid: runway-month-arithmetic
length = calendar.monthrange(today.year, today.month)[1]
# ruleid: runway-month-arithmetic
gap = (a.year - b.year) * 12
# ruleid: runway-month-arithmetic
prev = today.month - 1
# ruleid: runway-month-arithmetic
following = today.month + 1
# ok: runway-month-arithmetic
later = dates.add_months(today, 1)
# ok: runway-month-arithmetic
later = today + relativedelta(days=7)
