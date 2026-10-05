"""Invariants over the production routing_rules.yaml.

These guard against regressions where a fallback chain ordering would
silently route a translation-ish prompt to a B-tier translator.
"""

import yaml

from echeneis.gateway.config import RoutingConfig

_PROD = RoutingConfig.from_yaml("config/routing_rules.yaml")

with open("config/litellm_config.yaml", encoding="utf-8") as _f:
    _REGISTERED = {m["model_name"] for m in yaml.safe_load(_f)["model_list"]}


class TestRoutedModelsAreRegistered:
    def test_every_routed_model_exists_in_litellm_config(self) -> None:
        # A model renamed or retired in litellm_config.yaml but still named
        # in a routing chain fails only at request time, as a wasted hop.
        routed: set[str] = set()
        for tier in _PROD.tiers.values():
            routed.update(tier.models.values())
            for task in tier.models:
                routed.update(tier.get_fallback_chain(task))
            if isinstance(tier.fallback, list):
                routed.update(tier.fallback)
        missing = routed - _REGISTERED
        assert not missing, f"routed but not registered: {sorted(missing)}"


class TestGeneralQaFallbackQuality:
    def test_cerebras_llama_8b_not_first_in_general_qa(self) -> None:
        # cerebras-llama-8b scored 3/6 on the translation-term-preservation
        # benchmark. Translation prompts that miss the classifier keywords
        # degrade to general_qa, so the first general_qa fallback must be
        # an A-tier model.
        fallback = _PROD.tiers["A"].fallback["general_qa"]
        assert fallback[0] != "cerebras-llama-8b", (
            f"general_qa fallback still starts with cerebras-llama-8b: {fallback}"
        )


class TestTranslationPathIsolation:
    def test_translation_fallback_excludes_cerebras_llama_8b(self) -> None:
        # The dedicated translation path must never touch the B-tier
        # translator, even on main-model failure.
        fallback = _PROD.tiers["A"].fallback["translation"]
        assert "cerebras-llama-8b" not in fallback, (
            f"translation fallback contains cerebras-llama-8b: {fallback}"
        )
