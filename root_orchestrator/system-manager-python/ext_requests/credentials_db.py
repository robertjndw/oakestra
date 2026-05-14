import logging
from datetime import datetime

from bson import ObjectId

import ext_requests.mongodb_client as db

logger = logging.getLogger("system_manager")


def mongo_create_credential(record: dict) -> str:
    now = datetime.utcnow().isoformat()
    record["created_at"] = now
    record["updated_at"] = now
    result = db.mongo_credentials.insert_one(record)
    logger.info(f"MONGODB - credential inserted: {result.inserted_id}")
    return str(result.inserted_id)


def mongo_get_credential_by_id(credential_id: str) -> dict | None:
    try:
        return db.mongo_credentials.find_one({"_id": ObjectId(credential_id)})
    except Exception:
        return None


def mongo_get_credential_by_name_and_owner(name: str, owner_user_id: str) -> dict | None:
    return db.mongo_credentials.find_one(
        {"name": name, "owner_user_id": owner_user_id, "scope": "private"}
    )


def mongo_get_credential_by_name_and_org(name: str, organization_id: str) -> dict | None:
    return db.mongo_credentials.find_one(
        {"name": name, "organization_id": organization_id, "scope": "organization"}
    )


def mongo_list_credentials(owner_user_id: str, organization_id: str | None) -> list:
    query = {"owner_user_id": owner_user_id, "scope": "private"}
    private = list(db.mongo_credentials.find(query))
    org = []
    if organization_id:
        org = list(
            db.mongo_credentials.find(
                {"organization_id": organization_id, "scope": "organization"}
            )
        )
    return private + org


def mongo_update_credential(credential_id: str, updates: dict) -> dict | None:
    updates["updated_at"] = datetime.utcnow().isoformat()
    return db.mongo_credentials.find_one_and_update(
        {"_id": ObjectId(credential_id)},
        {"$set": updates},
        return_document=True,
    )


def mongo_delete_credential(credential_id: str) -> bool:
    result = db.mongo_credentials.find_one_and_delete({"_id": ObjectId(credential_id)})
    return result is not None
