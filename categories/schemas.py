from pydantic import BaseModel


class CategoryItem(BaseModel):
    slug: str
    label: str
    sort_order: int


class CategoryListResponse(BaseModel):
    items: list[CategoryItem]
