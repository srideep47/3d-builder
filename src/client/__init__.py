"""Client delivery layer — job intake, validator gates, packaging.

Knows about the client's *contract* (file names, size caps, tier ceilings,
units at the boundary). Knows nothing about any product: product nouns live
only in templates/<product_class>.yaml (GLM_BRIEF rule 11).
"""
