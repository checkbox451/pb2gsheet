import json
import os
import re
import time
from datetime import datetime
from itertools import chain
from pathlib import Path

import dateutil.parser
import pygsheets
import requests
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from telebot import TeleBot

accounts = [acc for acc in os.environ.get("ACCOUNTS", "").split(",") if acc]

sender_pat = re.compile(r"^.+,\s*(\S+\s+\S+\s+\S+)\s*$")

db_dir = os.environ.get("DB_DIR") or "."
transactions_file = Path(db_dir, "transactions.json")
bot_db = Path(db_dir, "checkbox451_bot.db")

service_account_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
spreadsheet_key = os.environ.get("GOOGLE_SPREADSHEET_KEY")
worksheet_title = os.environ.get("GOOGLE_WORKSHEET_TITLE")

bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")


class UserRole(declarative_base()):
    __tablename__ = "user_roles"
    user_id = Column(Integer, primary_key=True)
    role_name = Column(String(10), primary_key=True)


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

    dat_od = str(
        dateutil.parser.parse(
            transaction["DAT_OD"],
            dayfirst=True,
        ).date()
    )
    sum_e = float(transaction["SUM_E"])
    sender = get_sender(transaction["OSND"])
    wks.append_table([[dat_od, sum_e, sender]])


def bot_nofify(transaction):
    if bot_token is None or not bot_db.exists():
        return

    engine = create_engine(f"sqlite:///{bot_db}")
    session = sessionmaker(bind=engine)()

    supervisors = list(
        chain(
            *session.query(UserRole.user_id).filter_by(role_name="SUPERVISOR")
        )
    )
    if supervisors:
        bot = TeleBot(bot_token)

        sum_e = transaction["SUM_E"]
        sender = get_sender(transaction["OSND"])

        for user_id in supervisors:
            bot.send_message(
                user_id,
                f"Безготівкове зарахування: {sum_e} грн"
                + (f" від {sender}" if sender else ""),
            )


def new_transaction(prev, curr):
    prev_set = {frozenset(t.items()) for t in prev}
    curr_set = {frozenset(t.items()) for t in curr}

    new = [dict(i) for i in curr_set - prev_set]
    new.sort(
        key=lambda x: dateutil.parser.parse(
            x["DATE_TIME_DAT_OD_TIM_P"],
            dayfirst=True,
        )
    )

    return new


def process_transactions(prev):
    try:
        curr = get_transaction()
    except Exception as err:
        log(err)
        return prev

    if transactions := new_transaction(prev, curr):
        for transaction in transactions:
            if transaction["TRANTYPE"] == "C" and (
                not accounts or transaction["AUT_MY_ACC"] in accounts
            ):
                log(transaction)

                try:
                    store_transaction(transaction)
                except Exception as err:
                    log(err)
                    return prev

                write_transactions(curr)

                try:
                    bot_nofify(transaction)
                except Exception as err:
                    log(err)
    else:
        log("no new transactions")

    return curr


def main():
    prev = read_transactions()

    while True:
        prev = process_transactions(prev)
        time.sleep(60)


if __name__ == "__main__":
    main()
