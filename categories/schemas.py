from pydantic import BaseModel


class CategoryItem(BaseModel):
    slug: str
    label: str
    sort_order: int
    parent_slug: str | None = None


class CategoryListResponse(BaseModel):
    items: list[CategoryItem]
