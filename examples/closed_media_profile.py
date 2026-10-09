"""Restrict external resources to a domain-owned set of media types."""

from __future__ import annotations

import io
from typing import ClassVar

from tiergraph import (
    BLOB_NAMESPACE,
    AttributeValue,
    BlobProfile,
    Graph,
    GraphProfile,
    Item,
    NamespaceDeclaration,
    ProfileRegistry,
    QualifiedName,
    RoleBinding,
    SimpleRelationDeclaration,
    TierDeclaration,
    XsdType,
    declare_blob_vocabulary,
    hash_blob,
)

EXAMPLE_NAMESPACE = "urn:tiergraph:example:closed-media-profile"
"""Namespace for the declarations owned by this example."""

_RESOURCES = QualifiedName(EXAMPLE_NAMESPACE, "resources")
_RESOURCE_TYPE = QualifiedName(EXAMPLE_NAMESPACE, "resource")
_MEMBERS = QualifiedName(EXAMPLE_NAMESPACE, "resource-members")
_MEDIA_TYPE = QualifiedName(BLOB_NAMESPACE, "media-type")
_PAYLOAD = b"example resource payload"


def _blob_name(local: str) -> QualifiedName:
    """Return one name in the fixed blob namespace."""
    return QualifiedName(BLOB_NAMESPACE, local)


def resource_graph(media_type: str) -> Graph:
    """Build one external-resource graph with the requested media type."""
    ref = hash_blob(io.BytesIO(_PAYLOAD))
    editor = Graph((), (), ()).edit()
    declare_blob_vocabulary(editor)
    editor.declare(NamespaceDeclaration("media", EXAMPLE_NAMESPACE))
    editor.declare(TierDeclaration(_RESOURCES, "External resources"))
    editor.declare(SimpleRelationDeclaration(_MEMBERS, _RESOURCES, _RESOURCE_TYPE))
    editor.insert_item(
        _RESOURCES,
        0,
        Item(
            "example-resource",
            (
                AttributeValue(_blob_name("sha256"), XsdType.STRING, ref.sha256),
                AttributeValue(_blob_name("size"), XsdType.INTEGER, str(ref.size)),
                AttributeValue(_MEDIA_TYPE, XsdType.STRING, media_type),
                AttributeValue(
                    _blob_name("schema"),
                    XsdType.STRING,
                    "urn:tiergraph:example:resource-schema",
                ),
            ),
        ),
    )
    return editor.freeze()


class ClosedMediaTypes(GraphProfile):
    """Accept the blob vocabulary only for this domain's media-type set."""

    name = "example.closed-media-types"
    required_roles = ("vocabulary",)
    decides = (
        "the fixed external-resource vocabulary is valid",
        "every resource uses a media type supported by this domain",
    )
    allowed_media_types: ClassVar[tuple[str, ...]] = (
        "application/vnd.example.transcript+json",
        "audio/wav",
    )

    @classmethod
    def check(cls, graph: Graph, roles: RoleBinding) -> None:
        """Validate the blob profile and refuse media types outside the closed set."""
        if roles["vocabulary"] != BLOB_NAMESPACE:
            raise ValueError(f"role 'vocabulary' must bind {BLOB_NAMESPACE!r}")
        profile = BlobProfile(graph)
        tiers = {tier.declaration.name: tier for tier in graph.tiers}
        for reference, _ in profile.blobs():
            item = tiers[reference.tier].items[reference.index]
            media_type = next(
                attribute.lexical
                for attribute in item.attributes
                if isinstance(attribute, AttributeValue)
                and attribute.name == _MEDIA_TYPE
            )
            if media_type not in cls.allowed_media_types:
                allowed = ", ".join(cls.allowed_media_types)
                raise ValueError(
                    f"blob item {reference} uses media type {media_type!r}; "
                    f"allowed media types: {allowed}"
                )

    @classmethod
    def satisfaction_witness(cls) -> tuple[Graph, RoleBinding]:
        """Return a resource whose media type belongs to the domain."""
        return resource_graph("audio/wav"), {"vocabulary": BLOB_NAMESPACE}

    @classmethod
    def refusal_witness(cls) -> tuple[Graph, RoleBinding]:
        """Return a resource whose media type is outside the domain."""
        return resource_graph("video/mp4"), {"vocabulary": BLOB_NAMESPACE}


def main() -> int:
    """Register the domain profile and print its accepting and refusing reports."""
    registry = ProfileRegistry()
    registry.register(ClosedMediaTypes)
    roles: RoleBinding = {"vocabulary": BLOB_NAMESPACE}
    accepted = registry.report(
        ClosedMediaTypes.name, resource_graph("audio/wav"), roles
    )
    refused = registry.report(ClosedMediaTypes.name, resource_graph("video/mp4"), roles)
    print("allowed media type:", accepted.outcome.value)
    print("unlisted media type:", refused.outcome.value)
    return 0


__all__ = ["EXAMPLE_NAMESPACE", "ClosedMediaTypes", "main", "resource_graph"]


if __name__ == "__main__":
    raise SystemExit(main())
