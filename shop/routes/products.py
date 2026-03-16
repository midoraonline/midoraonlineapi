from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from core.schemas import PaginationParams
from db.supabase import get_supabase_client
from core.security import get_current_user_id, get_optional_user_id
from shop import service as shop_service
from shop.schemas import ProductCreate, ProductResponse, ProductUpdate

router = APIRouter()


@router.post("/{shop_id}/products", response_model=ProductResponse)
async def create_product(
  shop_id: str,
  body: ProductCreate,
  client: Annotated[any, Depends(get_supabase_client)],
  user_id: str = Depends(get_current_user_id),
):
    try:
        return shop_service.create_product(client, shop_id, body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{shop_id}/products")
async def list_products(
  shop_id: str,
  client: Annotated[any, Depends(get_supabase_client)],
  params: Annotated[PaginationParams, Depends()],
  category: str | None = None,
  search: str | None = None,
  user_id: str | None = Depends(get_optional_user_id),
):
    is_owner = False
    return shop_service.list_products(
        client, shop_id, page=params.page, limit=params.limit,
        category=category, search=search, is_owner=is_owner,
    )


router_products = APIRouter()


@router_products.get("/{product_id}", response_model=ProductResponse)
async def get_product(
  product_id: str,
  client: Annotated[any, Depends(get_supabase_client)],
):
    product = shop_service.get_product(client, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router_products.patch("/{product_id}", response_model=ProductResponse)
async def update_product(
  product_id: str,
  body: ProductUpdate,
  client: Annotated[any, Depends(get_supabase_client)],
  user_id: str = Depends(get_current_user_id),
):
    product = shop_service.update_product(client, product_id, body)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router_products.delete("/{product_id}")
async def delete_product(
  product_id: str,
  client: Annotated[any, Depends(get_supabase_client)],
  user_id: str = Depends(get_current_user_id),
):
    ok = shop_service.delete_product(client, product_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"deleted": product_id}


@router_products.post("/generate-from-image")
async def generate_from_image(
  client: Annotated[any, Depends(get_supabase_client)],
  user_id: str = Depends(get_current_user_id),
):
    """Merchant Copilot: Gemini suggests title, description, tags from image. Body: image_url or image_base64, shop_id."""
    return {"title": "", "description": "", "ai_seo_tags": ""}
