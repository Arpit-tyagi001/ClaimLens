from pydantic import BaseModel

from ai.claimlens_ai.llm import (
    LLMClient,
    MockLLMProvider,
)


class TestFinding(BaseModel):
    assessment: str
    confidence: float


def main():
    provider = MockLLMProvider(
        {
            "assessment": "SUPPORTED",
            "confidence": 0.91,
        }
    )

    client = LLMClient(provider)

    result = client.generate_structured(
        prompt="Determine whether the rejection is supported.",
        response_model=TestFinding,
    )

    print("LLM TEST PASSED")
    print(result)
    print(result.assessment)
    print(result.confidence)


if __name__ == "__main__":
    main()