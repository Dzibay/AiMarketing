"""Клиенты: CRUD, привязка кабинетов и счётчиков, авторазкладка новых объектов, вход по Яндексу."""

import pytest

from app.clients import (
    assign_direct_account,
    assign_metrika_counter,
    assign_new_items,
    create_client,
    delete_client,
    list_clients,
    update_client,
)
from app.users import get_user, upsert_yandex_user
from app.yandex import YandexError
from tests.dbutil import client_of, make_account, make_connection, make_counter, make_user


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"name": "  "}, "client-name"),
        ({"name": "A", "kpi_type": "roi"}, "kpi-type"),
        ({"name": "A", "kpi_type": "cpa", "kpi_target": -1}, "kpi-target"),
        ({"name": "A", "monthly_budget_rub": -5}, "budget"),
    ],
)
def test_create_validation(db, fields, code):
    user = make_user(db)
    with pytest.raises(YandexError) as exc:
        create_client(user, **fields)
    assert exc.value.code == code


def test_create_update_archive(db):
    user = make_user(db)
    client = create_client(
        user, name="Ромашка", site="romashka.ru", kpi_type="cpa", kpi_target=900, monthly_budget_rub=50000.5
    )
    assert client["kpi_target"] == 900.0
    assert client["monthly_budget_rub"] == 50000.5
    assert client["archived"] is False

    renamed = update_client(user, client["id"], name="Ромашка 2")
    assert renamed["name"] == "Ромашка 2"
    assert renamed["monthly_budget_rub"] == 50000.5  # не переданное поле не трогаем

    cleared = update_client(user, client["id"], kpi_target=None, archived=True)
    assert cleared["kpi_target"] is None
    assert cleared["archived"] is True
    assert update_client(user, client["id"], archived=False)["archived"] is False

    other = make_user(db, "other@example.com")
    with pytest.raises(YandexError):
        update_client(other, client["id"], name="чужой")
    assert list_clients(other) == []


def test_assign_and_delete_keeps_accounts(db):
    user = make_user(db)
    connection = make_connection(db, user)
    account = make_account(db, user, connection, name="Кабинет")
    counter = make_counter(db, user, connection, 101)
    client = create_client(user, name="Клиент")

    assert assign_direct_account(user, account, client["id"])["client_row_id"] == client["id"]
    assign_metrika_counter(user, counter, client["id"])
    listed = list_clients(user)[0]
    assert (listed["direct_count"], listed["metrika_count"]) == (1, 1)

    delete_client(user, client["id"])
    assert client_of(db, "direct_accounts", account) is None
    assert client_of(db, "metrika_counters", counter) is None


def test_cannot_assign_to_foreign_client_or_item(db):
    owner = make_user(db, "a@example.com")
    stranger = make_user(db, "b@example.com")
    connection = make_connection(db, owner)
    account = make_account(db, owner, connection)
    foreign_client = create_client(stranger, name="Чужой")
    with pytest.raises(YandexError):
        assign_direct_account(owner, account, foreign_client["id"])
    own_client = create_client(stranger, name="Свой")
    with pytest.raises(YandexError):
        assign_direct_account(stranger, account, own_client["id"])
    assert client_of(db, "direct_accounts", account) is None


def test_new_items_of_single_account_login_go_to_one_client(db):
    user = make_user(db)
    connection = make_connection(db, user, "client-login")
    account = make_account(db, user, connection, name="ООО Клиент")
    counter = make_counter(db, user, connection, 7, site="client.ru")

    assign_new_items(user, connection, new_account_ids=[account], new_counter_ids=[counter])

    client_id = client_of(db, "direct_accounts", account)
    assert client_id is not None
    assert client_of(db, "metrika_counters", counter) == client_id
    clients = list_clients(user)
    assert [(c["name"], c["site"]) for c in clients] == [("ООО Клиент", "client.ru")]

    # Ручная перекладка не откатывается повторным вызовом.
    manual = create_client(user, name="Другой")
    assign_direct_account(user, account, manual["id"])
    assign_new_items(user, connection, new_account_ids=[account], new_counter_ids=[])
    assert client_of(db, "direct_accounts", account) == manual["id"]
    assert len(list_clients(user)) == 2


def test_agency_login_counters_stay_unassigned(db):
    user = make_user(db)
    connection = make_connection(db, user, "agency")
    first = make_account(db, user, connection, name="Первый", login="first")
    second = make_account(db, user, connection, name="Второй", login="second")
    counter = make_counter(db, user, connection, 8)

    assign_new_items(user, connection, new_account_ids=[first, second], new_counter_ids=[counter])

    assert client_of(db, "direct_accounts", first) != client_of(db, "direct_accounts", second)
    assert client_of(db, "metrika_counters", counter) is None


def test_connecting_client_login_keeps_owner_identity(db):
    owner = make_user(db, "freelancer@example.com", yandex_uid="owner-uid")

    connected = upsert_yandex_user(yandex_uid="client-uid", existing_user_id=owner)
    assert connected["id"] == owner
    assert get_user(owner)["yandex_uid"] == "owner-uid"

    # Клиент, войдя своим Яндексом, попадает в свой (новый) аккаунт, а не к фрилансеру.
    client_login = upsert_yandex_user(yandex_uid="client-uid")
    assert client_login["id"] != owner
