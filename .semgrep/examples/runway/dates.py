# runway/dates.py is where month arithmetic lives.
import calendar

from dateutil.relativedelta import relativedelta

# ok: runway-month-arithmetic
later = d + relativedelta(months=n)
# ok: runway-month-arithmetic
length = calendar.monthrange(d.year, d.month)[1]
# ok: runway-month-arithmetic
gap = (b.year - a.year) * 12 + b.month - a.month
