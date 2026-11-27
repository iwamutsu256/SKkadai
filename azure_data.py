import os
import uuid

from azure.core.exceptions import ResourceNotFoundError
from azure.data.tables import TableServiceClient, UpdateMode
from azure.identity import DefaultAzureCredential


TABLE_NAMES = {
    "users": "users",
    "problems": "problems",
    "answers": "answers",
    "comments": "comments",
    "likes": "likes",
    "bookmarks": "bookmarks",
    "activities": "learningactivity",
    "goals": "learninggoals",
    "groups": "studygroups",
    "members": "groupmembers",
}


class StorageConfigurationError(RuntimeError):
    pass


class AzureTableStore:
    def __init__(self, app):
        self.app = app
        self._service = None
        self._tables = {}

    def _get_service(self):
        if self._service is not None:
            return self._service
        connection_string = self.app.config["AZURE_STORAGE_CONNECTION_STRING"]
        account_url = self.app.config["AZURE_TABLES_ACCOUNT_URL"]
        if connection_string:
            self._service = TableServiceClient.from_connection_string(connection_string)
        elif account_url:
            self._service = TableServiceClient(
                endpoint=account_url,
                credential=DefaultAzureCredential(),
            )
        else:
            raise StorageConfigurationError(
                "Configure AZURE_TABLES_ACCOUNT_URL or AZURE_STORAGE_CONNECTION_STRING."
            )
        return self._service

    def ensure_tables(self):
        service = self._get_service()
        for table_name in TABLE_NAMES.values():
            if table_name not in self._tables:
                self._tables[table_name] = service.create_table_if_not_exists(table_name)

    def _table(self, collection):
        self.ensure_tables()
        table_name = TABLE_NAMES[collection]
        return self._tables[table_name], table_name

    def list(self, collection, **criteria):
        table, table_name = self._table(collection)
        entities = []
        for entity in table.query_entities(
            query_filter=f"PartitionKey eq '{table_name}'"
        ):
            value = dict(entity)
            value["id"] = value["RowKey"]
            if all(value.get(key) == expected for key, expected in criteria.items()):
                entities.append(value)
        return entities

    def get(self, collection, entity_id):
        table, table_name = self._table(collection)
        try:
            entity = table.get_entity(partition_key=table_name, row_key=str(entity_id))
        except ResourceNotFoundError:
            return None
        value = dict(entity)
        value["id"] = value["RowKey"]
        return value

    def add(self, collection, values, entity_id=None):
        table, table_name = self._table(collection)
        entity_id = str(entity_id or uuid.uuid4().hex)
        entity = {
            "PartitionKey": table_name,
            "RowKey": entity_id,
            **{key: value for key, value in values.items() if value is not None},
        }
        table.create_entity(entity=entity)
        entity["id"] = entity_id
        return entity

    def save(self, collection, entity_id, values):
        table, table_name = self._table(collection)
        entity = {
            "PartitionKey": table_name,
            "RowKey": str(entity_id),
            **{
                key: value
                for key, value in values.items()
                if value is not None
                and key not in {"id", "PartitionKey", "RowKey", "Timestamp", "etag", "odata.etag"}
            },
        }
        table.upsert_entity(entity=entity, mode=UpdateMode.REPLACE)
        entity["id"] = str(entity_id)
        return entity

    def delete(self, collection, entity_id):
        table, table_name = self._table(collection)
        try:
            table.delete_entity(partition_key=table_name, row_key=str(entity_id))
        except ResourceNotFoundError:
            return False
        return True


class MemoryTableStore:
    """In-memory Azure Table-compatible store used only by automated tests."""

    def __init__(self):
        self.entities = {collection: {} for collection in TABLE_NAMES}

    def ensure_tables(self):
        return None

    def list(self, collection, **criteria):
        return [
            dict(entity)
            for entity in self.entities[collection].values()
            if all(entity.get(key) == expected for key, expected in criteria.items())
        ]

    def get(self, collection, entity_id):
        entity = self.entities[collection].get(str(entity_id))
        return dict(entity) if entity else None

    def add(self, collection, values, entity_id=None):
        entity_id = str(entity_id or uuid.uuid4().hex)
        if entity_id in self.entities[collection]:
            raise ValueError(f"Duplicate row key in {collection}: {entity_id}")
        entity = {"id": entity_id, **values}
        self.entities[collection][entity_id] = entity
        return dict(entity)

    def save(self, collection, entity_id, values):
        entity_id = str(entity_id)
        entity = {"id": entity_id, **values}
        self.entities[collection][entity_id] = entity
        return dict(entity)

    def delete(self, collection, entity_id):
        return self.entities[collection].pop(str(entity_id), None) is not None


def storage_urls():
    storage_url = os.environ.get("AZURE_STORAGE_ACCOUNT_URL", "").rstrip("/")
    table_url = os.environ.get("AZURE_TABLES_ACCOUNT_URL", "").rstrip("/")
    if not table_url and storage_url:
        table_url = storage_url.replace(".blob.", ".table.")
    return storage_url, table_url
