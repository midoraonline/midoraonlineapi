from fastapi import APIRouter

from ai.routes import chat, context, images, listing

router = APIRouter()
router.include_router(context.router, prefix="/shops", tags=["ai-context"])
router.include_router(chat.router, prefix="/chat", tags=["chat"])
router.include_router(images.router, prefix="/ai", tags=["ai-images"])
router.include_router(listing.router, prefix="/ai/listing", tags=["ai-listing"])
