import pytest
from azure.core.exceptions import ResourceNotFoundError
from flask import Flask

import azure_data
from azure_data import AzureTableStore, StorageConfigurationError


class FakeTable:
    def __init__(self, name):
        self.name = name
        self.entities = {}

    def query_entities(self, query_filter=None):
        return list(self.entities.values())

    def create_entity(self, entity):
        key = entity["RowKey"]
        if key in self.entities:
            raise ValueError("duplicate key")
        self.entities[key] = dict(entity)

    def get_entity(self, partition_key, row_key):
        if row_key not in self.entities:
            raise ResourceNotFoundError(message="not found")
        return dict(self.entities[row_key])

    def upsert_entity(self, entity, mode):
        self.entities[entity["RowKey"]] = dict(entity)

    def delete_entity(self, partition_key, row_key):
        if row_key not in self.entities:
            raise ResourceNotFoundError(message="not found")
        del self.entities[row_key]


class FakeTableService:
    def __init__(self):
        self.tables = {}

    def create_table_if_not_exists(self, name):
        return self.tables.setdefault(name, FakeTable(name))


def test_azure_table_store_crud(monkeypatch):
    service = FakeTableService()
    monkeypatch.setattr(
        azure_data.TableServiceClient,
        "from_connection_string",
        lambda value: service,
    )
    app = Flask(__name__)
    app.config["AZURE_STORAGE_CONNECTION_STRING"] = "test-connection"
    app.config["AZURE_TABLES_ACCOUNT_URL"] = ""
    store = AzureTableStore(app)

    created = store.add("problems", {"title": "test", "group_id": None}, "problem-1")
    assert created["id"] == "problem-1"
    assert store.get("problems", "problem-1")["title"] == "test"
    assert store.list("problems", title="test") == [created]

    store.save("problems", "problem-1", {"title": "updated", "id": "ignored"})
    assert store.get("problems", "problem-1")["title"] == "updated"
    assert store.delete("problems", "problem-1")
    assert store.get("problems", "problem-1") is None


def test_azure_table_store_requires_configuration():
    app = Flask(__name__)
    app.config["AZURE_STORAGE_CONNECTION_STRING"] = ""
    app.config["AZURE_TABLES_ACCOUNT_URL"] = ""
    store = AzureTableStore(app)

    with pytest.raises(StorageConfigurationError):
        store.ensure_tables()
