from types import SimpleNamespace

import numpy as np

from api.db.services.llm_service import LLMBundle
from rag.llm.musemind_gemini import GeminiPassage


class Observation:
    def __init__(self):
        self.updates = []
        self.ended = False

    def update(self, **kwargs):
        self.updates.append(kwargs)

    def end(self):
        self.ended = True


def _bundle(model):
    bundle = object.__new__(LLMBundle)
    bundle.mdl = model
    bundle.langfuse = object()
    bundle.trace_context = None
    bundle.model_config = {"llm_name": "pinned", "llm_factory": "Gemini"}
    observation = Observation()
    calls = []
    bundle._start_langfuse_observation = lambda **kwargs: calls.append(kwargs) or observation
    return bundle, observation, calls


def test_managed_gemini_embedding_passes_binary_and_omits_content_from_trace():
    inputs = [GeminiPassage("secret-title", "secret-text"), b"secret-image"]
    model = SimpleNamespace(
        manages_embedding_inputs=True,
        encode=lambda actual: (np.ones((len(actual), 3072), dtype=np.float32), 3) if actual is inputs else (_ for _ in ()).throw(AssertionError()),
    )
    bundle, observation, calls = _bundle(model)

    vectors, tokens = bundle.encode(inputs)

    assert vectors.shape == (2, 3072)
    assert tokens == 3
    assert calls[0]["input"] == {"item_count": 2, "content_omitted": True}
    assert observation.ended is True
    assert "secret" not in repr(calls)


def test_managed_gemini_cv_omits_prompt_and_output_from_trace():
    model = SimpleNamespace(
        content_free_observability=True,
        describe_with_prompt=lambda image, prompt: ("secret-output", 4),
    )
    bundle, observation, calls = _bundle(model)

    assert bundle.describe_with_prompt(b"secret-image", "secret-prompt") == "secret-output"

    assert calls[0]["metadata"] == {"model": "pinned"}
    assert observation.updates == [{"output": {"content_omitted": True}, "usage_details": {"total_tokens": 4}}]
    assert "secret" not in repr(calls)
