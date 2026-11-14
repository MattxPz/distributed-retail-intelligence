from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StockItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int = Field(ge=1, le=10, strict=True)
    quantity: int = Field(ge=0, strict=True)


class InitialSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    branch_id: int = Field(ge=1, le=3, strict=True)
    sequence: Literal[1] = 1
    schema_version: Literal[1] = 1
    snapshot_version: Literal[1] = 1
    event_type: Literal["inventory.initialized"] = "inventory.initialized"
    occurred_at: datetime
    items: list[StockItem]

    @field_validator("occurred_at")
    @classmethod
    def utc_only(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("Event timestamp must be UTC")
        return value

    @field_validator("items")
    @classmethod
    def complete_catalog(cls, value: list[StockItem]) -> list[StockItem]:
        if len(value) != 10 or {item.product_id for item in value} != set(range(1, 11)):
            raise ValueError("Initial snapshot must contain each of the ten products once")
        return sorted(value, key=lambda item: item.product_id)
