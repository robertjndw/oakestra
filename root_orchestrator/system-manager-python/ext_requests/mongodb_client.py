import logging
import os

from flask_pymongo import PyMongo

MONGO_URL = os.environ.get("ROOT_MONGO_URL", "localhost")
MONGO_PORT = os.environ.get("ROOT_MONGO_PORT", 10007)

MONGO_ADDR_USERS = f"mongodb://{MONGO_URL}:{MONGO_PORT}/users"

mongo_users = None
mongo_organization = None
mongo_credentials = None

app = None

CLUSTERS_FRESHNESS_INTERVAL = 45

logger = logging.getLogger("system_manager")


def mongo_init(flask_app):
    global app, mongo_users, mongo_organization, mongo_credentials

    app = flask_app

    _db = PyMongo(app, uri=MONGO_ADDR_USERS).db
    mongo_users = _db["user"]
    mongo_organization = _db["organization"]
    mongo_credentials = _db["credentials"]

    from pymongo import ASCENDING

    mongo_credentials.create_index(
        [("name", ASCENDING), ("owner_user_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"scope": "private"},
        background=True,
    )
    mongo_credentials.create_index(
        [("name", ASCENDING), ("organization_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"scope": "organization"},
        background=True,
    )

    logger.info("MONGODB - init mongo")
    logger.info(mongo_users)
