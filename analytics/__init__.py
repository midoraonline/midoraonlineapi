"""Platform-wide analytics module.

Owns the generic `analytics_events` log (migration 035) and admin insight
queries that read from it. Existing listing/impression events keep their
current homes; this module adds the broader surface described in the
insights doc (search, verification funnel, ratings coverage, follow
follow-through, stale-listing rate, view→WhatsApp click, etc).
"""
