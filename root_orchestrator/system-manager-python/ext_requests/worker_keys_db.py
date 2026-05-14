import logging
from datetime import datetime

import ext_requests.mongodb_client as db

logger = logging.getLogger("system_manager")


def mongo_upsert_worker_key(
    worker_id: str, cluster_id: str, pub_keyset_b64: str, key_id: str
) -> None:
    db.mongo_worker_keys.find_one_and_update(
        {"worker_id": worker_id},
        {
            "$set": {
                "worker_id": worker_id,
                "cluster_id": cluster_id,
                "pub_keyset_b64": pub_keyset_b64,
                "key_id": key_id,
                "registered_at": datetime.utcnow().isoformat(),
            }
        },
        upsert=True,
    )
    logger.info(f"MONGODB - worker key registered for worker_id={worker_id} key_id={key_id}")


def mongo_get_worker_key(worker_id: str) -> dict | None:
    return db.mongo_worker_keys.find_one({"worker_id": worker_id})
