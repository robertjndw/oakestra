from abc import ABC, abstractmethod


class CredentialTypeHandler(ABC):
    """
    Base class for all credential type handlers.

    A handler defines the validation logic and serialization rules for one
    credential type.  Adding a new type is as simple as subclassing this,
    overriding the abstract members, and calling `registry.register()`.
    """

    @property
    @abstractmethod
    def type_name(self) -> str:
        """Unique type discriminator stored in MongoDB, e.g. 'DockerRegistry'."""
        ...

    @property
    @abstractmethod
    def valid_uses(self) -> set:
        """Set of valid 'use_as' strings for this type, e.g. {'image_pull'}."""
        ...

    @abstractmethod
    def validate(self, payload: dict, metadata: dict) -> None:
        """Raise ValueError with a user-readable message if input is invalid."""
        ...

    @abstractmethod
    def public_view(self, record: dict) -> dict:
        """
        Return the API representation of a credential record.
        Must never include secret material (password, token, etc.).
        """
        ...

    @abstractmethod
    def materialize(self, payload: dict, metadata: dict, use_as: str) -> dict:
        """
        Produce the JSON-serialisable value that travels to the worker.
        This is what the worker deserializes and hands to its consumer.
        """
        ...
