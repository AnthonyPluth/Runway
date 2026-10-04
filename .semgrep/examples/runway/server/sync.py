# Sync and provider modules take the day they're given.
import datetime
from datetime import date

# ruleid: runway-today-in-sync
start = date.today()
# ruleid: runway-today-in-sync
start = datetime.date.today()
# ok: runway-today-in-sync
today = today or date.today()
# ok: runway-today-in-sync
start = today
