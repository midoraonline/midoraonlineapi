from fastapi import APIRouter

from shop.routes import orders, products

router = APIRouter()
router.include_router(products.router, prefix="/shops", tags=["products"])
router.include_router(products.router_products, tags=["products"])
router.include_router(orders.router, tags=["orders"])
