from fastapi import APIRouter

from shop.routes import engagement, orders, products

router = APIRouter()
router.include_router(engagement.router, prefix="/shops", tags=["shop-engagement"])
router.include_router(products.router, prefix="/shops", tags=["products"])
# Under /products so /api/v1/shops is not captured by /{product_id}
router.include_router(products.router_products, prefix="/products", tags=["products"])
router.include_router(orders.router, tags=["orders"])
