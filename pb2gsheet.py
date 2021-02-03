import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import dateutil.parser
import pygsheets
import requests

accounts = [acc for acc in os.environ.get("ACCOUNTS", "").split(",") if acc]

sender_pat = re.compile(r", (\S+ \S+ \S+)$")

db_dir = os.environ.get("DB_DIR") or "."
transactions_file = Path(db_dir, "transactions.json")

service_account_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
spreadsheet_key = os.environ.get("GOOGLE_SPREADSHEET_KEY")
worksheet_title = os.environ.get("GOOGLE_WORKSHEET_TITLE")


def log(msg):
    now = datetime.now().replace(microsecond=0)
    print(f"{now}: {msg}")


def get_transaction():
    transactions = []

    exist_next_page = True
    next_page_id = ""
    while exist_next_page:
        response = requests.get(
            "https://acp.privatbank.ua/api/statements/transactions/interim",
            headers={
                "id": os.environ["PRIVAT_API_ID"],
                "token": os.environ["PRIVAT_API_TOKEN"],
                "User-Agent": "Checkbox451 v0.1",
                "Content-Type": "application/json; charset=utf8",
            },
            params={
                "followId": next_page_id,
            },
        )
        result = response.json()

        transactions += result["transactions"]

        if exist_next_page := result["exist_next_page"]:
            next_page_id = result["next_page_id"]

    return transactions


def write_transactions(transactions):
    with transactions_file.open("w") as w:
        json.dump(transactions, w)


def read_transactions():
    if not transactions_file.exists():
        transaction = get_transaction()
        write_transactions(transaction)
        return transaction

    with transactions_file.open() as r:
        return json.load(r)


def get_sender(osnd):
    if match := sender_pat.match(osnd):
        return match.group(1).title()
    return ""


def store_transaction(transaction):
    if service_account_file is None:
        return

    client = pygsheets.authorize(service_account_file=service_account_file)
    spreadsheet = client.open_by_key(spreadsheet_key)
    wks = spreadsheet.worksheet_by_title(worksheet_title)

    dat_od = str(dateutil.parser.parse(transaction["DAT_OD"]).date())
    sum_e = float(transaction["SUM_E"])
    sender = get_sender(transaction["OSND"])
    wks.append_table([[dat_od, sum_e, sender]])


def main():
    prev = read_transactions()

    while True:
        curr = get_transaction()

        if curr != prev:
            write_transactions(curr)

        if len(curr) > (idx := len(prev)):
            for transaction in curr[idx:]:
                if (
                    not accounts or transaction["AUT_MY_ACC"] in accounts
                ) and transaction["TRANTYPE"] == "C":
                    log(transaction)
                    store_transaction(transaction)
        else:
            log("no transactions")

        prev = curr

        time.sleep(60)


if __name__ == "__main__":
    main()
