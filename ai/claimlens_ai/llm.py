from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

load_dotenv()

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ============================================================
# ERRORS
# ============================================================

class LLMError(Exception):
    """Raised when an LLM cannot produce valid output."""


# ============================================================
# RESPONSE
# ============================================================

@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


# ============================================================
# PROVIDER INTERFACE
# ============================================================

class LLMProvider(Protocol):

    def generate(self, prompt: str) -> LLMResponse:
        ...


# ============================================================
# MOCK PROVIDER
# ============================================================

class MockLLMProvider:

    def __init__(self, response: dict[str, Any]):
        self.response = response

    def generate(self, prompt: str) -> LLMResponse:

        return LLMResponse(
            text=json.dumps(self.response),
            input_tokens=0,
            output_tokens=0,
        )


# ============================================================
# GROQ PROVIDER
# INVESTIGATOR
# ============================================================

class GroqLLMProvider:
    """Groq provider used by the Investigator."""

    def __init__(
        self,
        model: str = "openai/gpt-oss-120b",
        timeout: float = 30.0,
    ):

        try:
            from groq import Groq

        except ImportError as exc:

            raise LLMError(
                "Groq SDK is not installed. "
                "Run: pip install groq"
            ) from exc

        self.api_key = os.getenv("GROQ_API_KEY")

        if not self.api_key:
            raise LLMError(
                "GROQ_API_KEY is not set."
            )

        self.model = model

        self.client = Groq(
            api_key=self.api_key,
            timeout=timeout,
        )

    def generate(self, prompt: str) -> LLMResponse:

        try:

            response = self.client.chat.completions.create(
                model=self.model,

                messages=[
                    {
                        "role": "system",
                        "content": (
                            """
                            You are a structured JSON generation model.

                            Return ONLY one valid JSON object.

                            IMPORTANT:
                            - Do not call tools.
                            - Do not use function calling.
                            - Do not use repository tools.
                            - Do not use code search.
                            - Do not invent external tools.
                            - Follow the JSON schema exactly.
                            """
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],

                temperature=0,

                max_tokens=1000,
                
                tool_choice="none",

                response_format={
                    "type": "json_object"
                },
            )

            content = response.choices[0].message.content

            if not content:

                raise LLMError(
                    "Groq returned an empty response."
                )

            usage = response.usage

            return LLMResponse(

                text=content,

                input_tokens=(
                    usage.prompt_tokens
                    if usage
                    else 0
                ),

                output_tokens=(
                    usage.completion_tokens
                    if usage
                    else 0
                ),
            )

        except LLMError:

            raise

        except Exception as exc:

            raise LLMError(
                f"Groq API request failed: {exc}"
            ) from exc


# ============================================================
# GEMINI PROVIDER
# ADVERSARY
# ============================================================

class GeminiLLMProvider:
    """Gemini provider used by the Adversary."""

    def __init__(
        self,
        model: str = "gemini-3.8-flash",
        timeout: float = 30.0,
    ):

        try:
            from google import genai

        except ImportError as exc:

            raise LLMError(
                "Gemini SDK is not installed. "
                "Run: pip install google-genai"
            ) from exc

        self.api_key = os.getenv("GEMINI_API_KEY")

        if not self.api_key:

            raise LLMError(
                "GEMINI_API_KEY is not set."
            )

        self.model = model

        self.client = genai.Client(
            api_key=self.api_key
        )

        self.timeout = timeout

    def generate(self, prompt: str) -> LLMResponse:

        last_error = None

        # Retry temporary Gemini failures
        for attempt in range(3):

            try:

                from google.genai import types

                response = self.client.models.generate_content(

                    model=self.model,

                    contents=prompt,

                    config=types.GenerateContentConfig(

                        temperature=0,

                        response_mime_type="application/json",

                    ),
                )

                content = response.text

                if not content:

                    raise LLMError(
                        "Gemini returned an empty response."
                    )

                usage = getattr(
                    response,
                    "usage_metadata",
                    None,
                )

                return LLMResponse(

                    text=content,

                    input_tokens=getattr(
                        usage,
                        "prompt_token_count",
                        0,
                    ),

                    output_tokens=getattr(
                        usage,
                        "candidates_token_count",
                        0,
                    ),
                )

            except LLMError:

                raise

            except Exception as exc:

                last_error = exc

                error_text = str(exc)

                # Retry temporary server/rate-limit errors
                if (
                    (
                        "503" in error_text
                        or "UNAVAILABLE" in error_text
                        or "429" in error_text
                        or "RESOURCE_EXHAUSTED" in error_text
                    )
                    and attempt < 2
                ):

                    wait_time = 2 ** attempt

                    logger.warning(
                        "Gemini temporary error. "
                        "Retrying in %s seconds...",
                        wait_time,
                    )

                    time.sleep(wait_time)

                    continue

                raise LLMError(
                    f"Gemini API request failed: {exc}"
                ) from exc

        raise LLMError(
            "Gemini API request failed after retries: "
            f"{last_error}"
        )


# ============================================================
# LLM CLIENT
# ============================================================

class LLMClient:
    """
    ClaimLens LLM client.

    Responsibilities:

    1. Call provider
    2. Parse JSON
    3. Validate with Pydantic
    4. Perform one repair retry
    5. Log token usage
    """

    def __init__(
        self,
        provider: LLMProvider,
        max_retries: int = 1,
    ):

        self.provider = provider

        self.max_retries = max_retries

    def generate_structured(
        self,
        prompt: str,
        response_model: type[T],
    ) -> T:

        # ----------------------------------------------------
        # FIRST LLM CALL
        # ----------------------------------------------------

        response = self.provider.generate(prompt)

        self._log_tokens(response)

        try:

            return self._parse_and_validate(
                response.text,
                response_model,
            )

        except (
            json.JSONDecodeError,
            ValidationError,
        ) as first_error:

            # ------------------------------------------------
            # REPAIR RETRY
            # ------------------------------------------------

            if self.max_retries < 1:

                raise LLMError(
                    "LLM returned invalid structured output: "
                    f"{first_error}"
                ) from first_error

            repair_prompt = self._build_repair_prompt(

                original_prompt=prompt,

                bad_response=response.text,

                error=first_error,
            )

            logger.warning(
                "LLM output invalid. "
                "Attempting one repair."
            )

            repaired_response = self.provider.generate(
                repair_prompt
            )

            self._log_tokens(
                repaired_response
            )

            try:

                return self._parse_and_validate(

                    repaired_response.text,

                    response_model,
                )

            except (
                json.JSONDecodeError,
                ValidationError,
            ) as second_error:

                raise LLMError(
                    "LLM output remained invalid "
                    "after one repair attempt."
                ) from second_error

    # ========================================================
    # JSON + PYDANTIC VALIDATION
    # ========================================================

    @staticmethod
    def _parse_and_validate(
        text: str,
        response_model: type[T],
    ) -> T:

        data = json.loads(text)

        return response_model.model_validate(
            data
        )

    # ========================================================
    # REPAIR PROMPT
    # ========================================================

    @staticmethod
    def _build_repair_prompt(
        original_prompt: str,
        bad_response: str,
        error: Exception,
    ) -> str:

        return f"""
You are repairing an invalid JSON response.

ORIGINAL TASK:
{original_prompt}

INVALID RESPONSE:
{bad_response}

VALIDATION ERROR:
{error}

Return ONLY ONE valid JSON object.

Do not use markdown.
Do not use code fences.
Do not add explanations.
Do not return an empty response.
""".strip()

    # ========================================================
    # TOKEN LOGGING
    # ========================================================

    @staticmethod
    def _log_tokens(
        response: LLMResponse,
    ) -> None:

        logger.info(
            "LLM usage: input_tokens=%s output_tokens=%s",

            response.input_tokens,

            response.output_tokens,
        )


# ============================================================
# PROVIDER FACTORIES
# ============================================================

def create_llm_provider() -> LLMProvider:
    """
    Create provider from environment configuration.
    """

    mock_ai = os.getenv(
        "MOCK_AI",
        "0",
    ).lower()

    # --------------------------------------------------------
    # MOCK MODE
    # --------------------------------------------------------

    if mock_ai in {
        "1",
        "true",
        "yes",
    }:

        return MockLLMProvider(

            response={
                "message": "Mock ClaimLens response"
            }

        )

    # --------------------------------------------------------
    # REAL PROVIDER
    # --------------------------------------------------------

    provider = os.getenv(
        "LLM_PROVIDER",
        "groq",
    ).lower()

    if provider == "groq":

        return create_groq_provider()

    if provider == "gemini":

        return create_gemini_provider()

    raise LLMError(
        f"Unsupported LLM_PROVIDER: {provider}. "
        "Use 'groq' or 'gemini'."
    )


# ============================================================
# GROQ FACTORY
# ============================================================

def create_groq_provider() -> GroqLLMProvider:

    return GroqLLMProvider(

        model=os.getenv(
            "GROQ_MODEL",
            "openai/gpt-oss-120b",
        )

    )


# ============================================================
# GEMINI FACTORY
# ============================================================

def create_gemini_provider() -> GeminiLLMProvider:

    return GeminiLLMProvider(

        model=os.getenv(
            "GEMINI_MODEL",
            "gemini-3.8-flash",
        )

    )