"""Typed API request contracts."""
from pydantic import BaseModel, Field, field_validator, model_validator


class WarehouseCreate(BaseModel):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)


class ProductCreate(BaseModel):
    sku: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=160)
    price_cents: int = Field(ge=0, le=100_000_000)
    reorder_point: int = Field(default=5, ge=0, le=1_000_000)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    price_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    reorder_point: int | None = Field(default=None, ge=0, le=1_000_000)
    active: bool | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Update values cannot be null")
        return self


class Adjustment(BaseModel):
    product_id: int = Field(gt=0)
    warehouse_id: int = Field(gt=0)
    delta: int = Field(ge=-1_000_000, le=1_000_000)
    reason: str = Field(min_length=2, max_length=200)

    @field_validator("delta")
    @classmethod
    def nonzero(cls, value):
        if value == 0:
            raise ValueError("Stock change cannot be zero")
        return value


class Transfer(BaseModel):
    product_id: int = Field(gt=0)
    from_warehouse_id: int = Field(gt=0)
    to_warehouse_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=1_000_000)

    @model_validator(mode="after")
    def distinct(self):
        if self.from_warehouse_id == self.to_warehouse_id:
            raise ValueError("Source and destination must differ")
        return self


class OrderLine(BaseModel):
    product_id: int = Field(gt=0)
    warehouse_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=1_000_000)


class OrderCreate(BaseModel):
    customer_name: str = Field(min_length=1, max_length=160)
    lines: list[OrderLine] = Field(min_length=1, max_length=100)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)
