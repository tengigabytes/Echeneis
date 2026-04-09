"""Task classifier and tiered routing engine.

Classifies incoming requests by task type and routes them
to the appropriate model tier (S/A/B) with per-provider
rate limit awareness and automatic failover.
"""
