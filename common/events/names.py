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
    # Fired last, AFTER PRODUCT_CREATED/UPDATED, so the (slow) inline
    # moderation pipeline runs only once mail/ranking/embeddings have
    # finished. Splitting the trigger from the enqueue keeps the queue
    # row safe even when the pipeline is killed by a serverless timeout.
    PRODUCT_MODERATE_NOW = "product.moderate_now"
    PRODUCT_STATUS_CHANGED = "product.status_changed"
    SHOP_VERIFICATION_CHANGED = "shop.verification_changed"

