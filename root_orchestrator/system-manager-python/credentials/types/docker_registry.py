from credentials.types import CredentialTypeHandler


class DockerRegistryHandler(CredentialTypeHandler):
    """
    Credential type for OCI / Docker registry authentication.

    Storage split:
      - metadata (public): username, registry host, optional comment
      - payload (encrypted): password or access token

    Supported uses: image_pull
    """

    type_name = "DockerRegistry"

    # Registry host assumed when a credential omits one (Docker Hub).
    DEFAULT_REGISTRY = "docker.io"

    valid_uses = {"image_pull"}

    def validate(self, payload: dict, metadata: dict) -> None:
        if not payload.get("password"):
            raise ValueError("DockerRegistry 'data.password' must not be empty")
        if not metadata.get("username"):
            raise ValueError("DockerRegistry 'metadata.username' must not be empty")

    def public_view(self, record: dict) -> dict:
        meta = record.get("metadata", {})
        return {
            "_id": str(record["_id"]),
            "name": record["name"],
            "type": record["type"],
            "scope": record["scope"],
            "username": meta.get("username"),
            "registry": meta.get("registry", self.DEFAULT_REGISTRY),
            "comment": meta.get("comment"),
            "owner_user_id": record.get("owner_user_id"),
            "organization_id": record.get("organization_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
        }

    def materialize(self, payload: dict, metadata: dict, use_as: str) -> dict:
        return {
            "username": metadata.get("username"),
            "password": payload["password"],
            "registry": metadata.get("registry", self.DEFAULT_REGISTRY),
        }
