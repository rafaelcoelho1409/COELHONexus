"""Offline check of the shared LLM-usage parser (`domains.settings.llm_usage.domain.extract_usage`).

    cd apps/server && .venv/bin/python ../../scripts/server/test_llm_usage.py

The expected values are what the three parsers it replaced (DD raw `.usage`, RR `LLMResult` + usage_metadata,
YCS `llm_output`) returned on the same inputs; the only deliberate differences are called out inline."""
import os
import sys
from pathlib import Path

for name, value in {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "x", "ENVIRONMENT": "local"}.items():
    os.environ.setdefault(name, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, LLMResult  # noqa: E402

import domains  # noqa: E402

extract = domains.settings.llm_usage.domain.extract_usage
Usage = domains.settings.llm_usage.entities.Usage


def llm(llm_output, **message):
    generations = [[ChatGeneration(message = AIMessage(content = "x", **message))]] if message else [[]]
    return LLMResult(generations = generations, llm_output = llm_output)


class RawResponse:
    def __init__(self, usage, model):
        self.usage, self.model = usage, model


class PydanticLike:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return self._data


def check(label, response, model, tokens_in, tokens_out, reasoning = 0):
    got = extract(response)
    assert got == Usage(model, tokens_in, tokens_out, reasoning), f"{label}: {got}"
    print(f"ok  {label}")


# ---- LangChain LLMResult (the on_llm_end payload) -----------------------------------------------------------------
check("llm_output token_usage + reasoning details (RR and YCS agreed)",
      llm({"token_usage": {"prompt_tokens": 7, "completion_tokens": 9, "completion_tokens_details": {"reasoning_tokens": 4}}, "model_name": "m"}),
      "m", 7, 9, 4)
check("usage_metadata wins over llm_output (RR behaviour; YCS used llm_output only, same numbers in practice)",
      llm({"token_usage": {"prompt_tokens": 11, "completion_tokens": 6}, "model_name": "m-out"},
          usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}, response_metadata = {"model_name": "m-meta"}),
      "m-out", 10, 5)
check("usage_metadata only, model from response_metadata (RR; YCS returned 0 tokens and 'unknown' here)",
      llm(None, usage_metadata = {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}, response_metadata = {"model_name": "m-meta"}),
      "m-meta", 3, 4)
check("llm_output `usage` key with input/output names (YCS; RR returned 0 tokens here)",
      llm({"usage": {"input_tokens": 3, "output_tokens": 2}, "model": "m4"}),
      "m4", 3, 2)
check("reasoning tokens from usage_metadata.output_token_details",
      llm({}, usage_metadata = {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3, "output_token_details": {"reasoning": 5}}),
      None, 1, 2, 5)
check("empty llm_output", llm({}), None, 0, 0)

# ---- raw provider / LiteLLM response (DD) -------------------------------------------------------------------------
check("raw dict response with reasoning details (DD)",
      {"usage": {"prompt_tokens": 5, "completion_tokens": 8, "completion_tokens_details": {"reasoning_tokens": 3}}, "model": "x"},
      "x", 5, 8, 3)
check("raw object with a pydantic-style usage (DD)",
      RawResponse(PydanticLike({"input_tokens": 2, "output_tokens": 6, "reasoning_tokens": 1}), "y"), "y", 2, 6, 1)
check("raw response without usage (DD)", {"model": "z"}, "z", 0, 0, 0)
check("no response at all (DD)", None, None, 0, 0, 0)

# ---- clamping -----------------------------------------------------------------------------------------------------
check("negative counts are clamped to zero", {"usage": {"prompt_tokens": -4, "completion_tokens": 2}}, None, 0, 2, 0)
print("ALL OK")
