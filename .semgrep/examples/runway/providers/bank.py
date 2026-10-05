# Every provider module (runway/providers/) takes the day it's given, as the sync does.
from datetime import date


def sync(conn, today=None):
    # ok: runway-today-in-sync
    today = today or date.today()
    return today


def statement(conn):
    # ruleid: runway-today-in-sync
    return date.today()
