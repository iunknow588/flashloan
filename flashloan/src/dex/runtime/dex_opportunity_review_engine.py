from __future__ import annotations

from dex.runtime.dex_opportunity_review_engine_fixed import DexOpportunityFixedRefreshMixin


class DexOpportunityReviewEngineMixin(DexOpportunityFixedRefreshMixin):
    """Shared fixed-refresh and manual-review helpers (split for line limits)."""
