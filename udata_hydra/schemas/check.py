import datetime
import json

from pydantic import UUID4, BaseModel, Field, field_validator


class CheckSchema(BaseModel):
    id: int = Field(alias="check_id")
    catalog_id: int | None = None
    url: str | None = None
    domain: str | None = None
    created_at: datetime.datetime
    status: int | None = Field(alias="check_status")
    headers: dict | None = {}
    timeout: bool | None = None
    response_time: float | None = None
    error: str | None = None
    dataset_id: str | None = None
    resource_id: UUID4 | None = None
    deleted: bool | None = None
    parsing_started_at: datetime.datetime | None = None
    parsing_finished_at: datetime.datetime | None = None
    parsing_error: str | None = None
    parsing_table: str | None = None
    parquet_url: str | None = None
    parquet_size: int | None = None

    @field_validator("headers", mode="before")
    @classmethod
    def transform(cls, headers: str | None) -> dict:
        if headers:
            return json.loads(headers)
        return {}


class CheckGroupBy(BaseModel):
    value: str
    count: int
