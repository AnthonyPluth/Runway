# Migrations are frozen as they ran: they keep the literals they were written with.

# ok: runway-half-cent-literal
if amount < 0.005:
    pass
# ok: runway-month-arithmetic
later = d + relativedelta(months=1)
