"""Canonical event names.

Keep these as constants so emitters and subscribers never drift on a
typo'd string. NestJS equivalent: `@OnEvent('product.created')`.
"""


class Events:
    PRODUCT_CREATED = "product.created"
    PRODUCT_UPDATED = "product.updated"
    # Fired from the product write route when the listing lands in
    # `pending_review`. Subscribers should ONLY do cheap work here
    # (enqueue the moderation row). Payload carries the product id.
    PRODUCT_PENDING_REVIEW = "product.pending_review"
    # Fired after PRODUCT_CREATED/UPDATED, off the request path, once
    # mail/ranking/embeddings have been scheduled. The queue row from
    # PRODUCT_PENDING_REVIEW is already stored before the response.
    PRODUCT_MODERATE_NOW = "product.moderate_now"
    PRODUCT_STATUS_CHANGED = "product.status_changed"
    SHOP_VERIFICATION_CHANGED = "shop.verification_changed"

