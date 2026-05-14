import logging
from datetime import datetime

import ext_requests.mongodb_client as db

logger = logging.getLogger("system_manager")


def mongo_upsert_worker_key(
    worker_id: str, cluster_id: str, pub_keyset_b64: str, key_id: str
) -> bool:
    """
    Register/update a worker's public keyset, scoped to the calling cluster.
    Refuses to overwrite an entry owned by a different cluster — without this guard,
    a malicious cluster could claim another cluster's worker_id and redirect
    subsequent credential seals to its own keypair.
    Returns True on success, False if the worker is already registered to a
    different cluster.
    """
    existing = db.mongo_worker_keys.find_one({"worker_id": worker_id}, {"cluster_id": 1})
    if existing is not None and existing.get("cluster_id") != cluster_id:
        logger.warning(
            f"MONGODB - refused worker key upsert for worker_id={worker_id} "
            f"from cluster_id={cluster_id}: worker already registered to cluster "
            f"{existing.get('cluster_id')}"
        )
        return False

    db.mongo_worker_keys.update_one(
        {"worker_id": worker_id, "cluster_id": cluster_id},
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
    return True


def mongo_get_worker_key(worker_id: str) -> dict | None:
    return db.mongo_worker_keys.find_one({"worker_id": worker_id})
