from credentials.types import CredentialTypeHandler

_registry: dict[str, CredentialTypeHandler] = {}


def register(handler: CredentialTypeHandler) -> None:
    _registry[handler.type_name] = handler


def get_handler(type_name: str) -> CredentialTypeHandler | None:
    return _registry.get(type_name)


def get_registered_types() -> list[str]:
    return list(_registry.keys())


def register_builtin() -> None:
    from credentials.types.docker_registry import DockerRegistryHandler

    register(DockerRegistryHandler())
