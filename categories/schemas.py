from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from categories.fields import normalize_fields


class CategoryMetaField(BaseModel):
    key: str
    label: str = ""
    type: str = "text"
    kind: str = "text"
    required: bool = False
    help_text: str | None = None
    placeholder: str | None = None
    options: list[dict[str, str]] | None = None
    partial: bool = False
    inherited: bool = False
    overridden: bool = False
    overrides: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, value):
        if not isinstance(value, dict):
            return value
        from categories.fields import normalize_one

        normalized = normalize_one(value)
        return normalized or value


class CategoryItem(BaseModel):
    slug: str
    label: str
    sort_order: int
    parent_slug: str | None = None
    metadata: list[CategoryMetaField] = []
    fields: list[CategoryMetaField] = []
    effective_fields: list[CategoryMetaField] = []

    @field_validator("metadata", "fields", "effective_fields", mode="before")
    @classmethod
    def _field_lists(cls, value):
        if value is None:
            return []
        return normalize_fields(value)

    @model_validator(mode="after")
    def _mirror_fields(self):
        if not self.fields:
            self.fields = list(self.metadata)
        if not self.effective_fields:
            self.effective_fields = list(self.fields)
        return self


class CategoryListResponse(BaseModel):
    items: list[CategoryItem]


class CategoryCountsResponse(BaseModel):
    """Published listing counts keyed by top-level parent category label."""

    counts: dict[str, int]


class CategoryFieldsResponse(BaseModel):
    slug: str
    label: str
    parent_slug: str | None = None
    fields: list[CategoryMetaField] = []


def _coerce_metadata(data):
    if not isinstance(data, dict):
        return data
    payload = dict(data)
    if payload.get("metadata") is None and "fields" in payload:
        payload["metadata"] = payload["fields"]
    if payload.get("metadata") is not None:
        payload["metadata"] = normalize_fields(payload["metadata"])
    return payload


class CategoryCreateRequest(BaseModel):
    slug: str
    label: str
    parent_slug: str | None = None
    sort_order: int = 0
    metadata: list[CategoryMetaField] = []

    @model_validator(mode="before")
    @classmethod
    def _fields_alias(cls, data):
        return _coerce_metadata(data)


class CategoryUpdateRequest(BaseModel):
    label: str | None = None
    parent_slug: str | None = None
    sort_order: int | None = None
    metadata: list[CategoryMetaField] | None = None

    @model_validator(mode="before")
    @classmethod
    def _fields_alias(cls, data):
        return _coerce_metadata(data)


class FieldDefinitionWrite(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str | None = None
    label: str | None = None
    type: str | None = None
    kind: str | None = None
    required: bool | None = None
    help_text: str | None = None
    placeholder: str | None = None
    options: list | None = None


class FieldReorderRequest(BaseModel):
    keys: list[str]
