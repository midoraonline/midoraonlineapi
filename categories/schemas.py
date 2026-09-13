from pydantic import BaseModel


class CategoryMetaField(BaseModel):
    key: str
    label: str
    kind: str = "text"
    required: bool = False
    placeholder: str | None = None
    options: list[dict[str, str]] | None = None


class CategoryItem(BaseModel):
    slug: str
    label: str
    sort_order: int
    parent_slug: str | None = None
    metadata: list[CategoryMetaField] = []


class CategoryListResponse(BaseModel):
    items: list[CategoryItem]


class CategoryCountsResponse(BaseModel):
    """Published listing counts keyed by top-level parent category label."""

    counts: dict[str, int]


class CategoryCreateRequest(BaseModel):
    slug: str
    label: str
    parent_slug: str | None = None
    sort_order: int = 0
    metadata: list[CategoryMetaField] = []


class CategoryUpdateRequest(BaseModel):
    label: str | None = None
    parent_slug: str | None = None
    sort_order: int | None = None
    metadata: list[CategoryMetaField] | None = None