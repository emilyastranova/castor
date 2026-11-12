import sqlite3

_conn = None


def connect():
    global _conn
    _conn = sqlite3.connect("shop.db")


def query(sql):
    return _conn.execute(sql).fetchall()
