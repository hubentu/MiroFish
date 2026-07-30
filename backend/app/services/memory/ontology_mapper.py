"""Map MiroFish ontology dicts to Graphiti Pydantic entity type models."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, create_model

from ...utils.ontology import (
    MAX_ONTOLOGY_TYPES,
    RESERVED_ONTOLOGY_ATTRIBUTE_NAMES,
    normalize_ontology_attributes,
)


def _safe_attr_name(attr_name: str) -> str:
    if attr_name.lower() in RESERVED_ONTOLOGY_ATTRIBUTE_NAMES:
        return f"entity_{attr_name}"
    return attr_name


def map_entity_types(ontology: dict[str, Any]) -> dict[str, type[BaseModel]]:
    """Convert MiroFish ``entity_types`` into Graphiti ``entity_types`` models.

    Attribute names colliding with Graphiti/EntityNode reserved fields are
    prefixed with ``entity_`` (same rule as ``graph_builder.set_ontology``).
    """
    entity_types: dict[str, type[BaseModel]] = {}
    for entity_def in ontology.get("entity_types", [])[:MAX_ONTOLOGY_TYPES]:
        if not isinstance(entity_def, dict):
            continue
        name = entity_def.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        description = entity_def.get("description") or f"A {name} entity."

        field_definitions: dict[str, Any] = {}
        for normalized in normalize_ontology_attributes(entity_def.get("attributes", [])):
            attr_name = _safe_attr_name(normalized["name"])
            attr_desc = normalized.get("description") or attr_name
            field_definitions[attr_name] = (
                Optional[str],
                Field(default=None, description=attr_desc),
            )

        model = create_model(name, __base__=BaseModel, **field_definitions)
        model.__doc__ = description
        entity_types[name] = model

    return entity_types
