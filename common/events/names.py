"""Canonical event names.

Keep these as constants so emitters and subscribers never drift on a
typo'd string. NestJS equivalent: `@OnEvent('product.created')`.
"""


class Events:
    PRODUCT_CREATED = "product.created"
    PRODUCT_UPDATED = "product.updated"
    # Fired from the product write route when the listing lands in
    # `pending_review`. Payload carries the product id — subscribers must
    # not poll the DB for pending rows.
    PRODUCT_PENDING_REVIEW = "product.pending_review"
    PRODUCT_STATUS_CHANGED = "product.status_changed"
    SHOP_VERIFICATION_CHANGED = "shop.verification_changed"

