"""Ollama-based router backend implementation."""

import json
import re

import httpx

from secureclaw.agent.router import (
    ROUTER_PROMPT,
    MessageIntent,
    RoutingDecision,
)
from secureclaw.config import get_settings
from secureclaw.logging import get_logger

log = get_logger("secureclaw.agent.router_ollama")


class OllamaRouterBackend:
    """Router backend using local Ollama container."""

    def __init__(self) -> None:
        """Initialize the Ollama router backend."""
        settings = get_settings()
        self._url = settings.ollama_url
        self._model = settings.ollama_router_model
        self._timeout = settings.ollama_timeout
        self._client = httpx.AsyncClient(timeout=self._timeout)
        log.info(
            "ollama_router_initialized",
            url=self._url,
            model=self._model,
            timeout=self._timeout,
        )

    async def classify(self, message: str) -> RoutingDecision:
        """Classify a message using Ollama.

        Args:
            message: The user's message to classify.

        Returns:
            RoutingDecision with intent and routing info.
        """
        try:
            # Format prompt
            prompt = f"{ROUTER_PROMPT}\n\nUser message: {message}"

            # Make request to Ollama
            response = await self._client.post(
                f"{self._url}/api/generate",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",  # Request JSON format
                    "options": {
                        "temperature": 0.1,  # Low for consistent classification
                        "num_predict": 150,
                    },
                },
            )
            response.raise_for_status()

            # Parse response
            result_data = response.json()
            result_text = result_data.get("response", "").strip()

            # Extract JSON using regex (same as Gemini router)
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", result_text, re.DOTALL)
            if json_match:
                result_text = json_match.group(1)
            else:
                # Try to find raw JSON object
                json_match = re.search(r"\{.*?\}", result_text, re.DOTALL)
                if json_match:
                    result_text = json_match.group(0)

            result_text = result_text.strip()

            # Parse JSON
            try:
                result = json.loads(result_text)
            except json.JSONDecodeError as e:
                log.warning(
                    "json_parse_failed",
                    error=str(e),
                    response_text=result_text[:200],
                )
                raise ValueError(f"Invalid JSON response: {e}") from e

            # Validate schema
            if "intent" not in result:
                raise ValueError("Missing 'intent' field in response")

            # Parse intent with error handling
            try:
                intent = MessageIntent(result["intent"].lower())
            except (ValueError, KeyError) as e:
                log.warning(
                    "invalid_intent",
                    intent_value=result.get("intent"),
                    error=str(e),
                )
                raise ValueError(f"Invalid intent value: {result.get('intent')}") from e

            confidence = float(result.get("confidence", 0.8))
            reasoning = result.get("reasoning", "")

            # Validate confidence range
            if not 0.0 <= confidence <= 1.0:
                log.warning("invalid_confidence", confidence=confidence)
                confidence = max(0.0, min(1.0, confidence))

            # Determine if we need Claude (expensive) or can use Flash (cheap)
            use_claude = intent == MessageIntent.COMPLEX_TASK and confidence > 0.7

            decision = RoutingDecision(
                intent=intent,
                confidence=confidence,
                reasoning=reasoning,
                use_claude=use_claude,
            )

            log.debug(
                "message_classified",
                intent=intent.value,
                confidence=confidence,
                use_claude=use_claude,
                backend="ollama",
            )

            return decision

        except httpx.TimeoutException as e:
            log.warning(
                "ollama_timeout",
                error=str(e),
                message=message[:50],
            )
            # Default to simple query on timeout
            return RoutingDecision(
                intent=MessageIntent.SIMPLE_QUERY,
                confidence=0.5,
                reasoning="Ollama timeout, using simple query as fallback",
                use_claude=False,
            )
        except httpx.ConnectError as e:
            log.error(
                "ollama_connection_failed",
                error=str(e),
                url=self._url,
                message=message[:50],
            )
            # Default to simple query on connection error
            return RoutingDecision(
                intent=MessageIntent.SIMPLE_QUERY,
                confidence=0.5,
                reasoning="Ollama connection failed, using simple query as fallback",
                use_claude=False,
            )
        except json.JSONDecodeError as e:
            log.warning(
                "json_decode_error",
                error=str(e),
                message=message[:50],
            )
            # Default to simple query on JSON errors
            return RoutingDecision(
                intent=MessageIntent.SIMPLE_QUERY,
                confidence=0.5,
                reasoning="JSON decode failed, using simple query as fallback",
                use_claude=False,
            )
        except ValueError as e:
            log.warning(
                "validation_error",
                error=str(e),
                message=message[:50],
            )
            # Default to simple query on validation errors
            return RoutingDecision(
                intent=MessageIntent.SIMPLE_QUERY,
                confidence=0.5,
                reasoning="Validation failed, using simple query as fallback",
                use_claude=False,
            )
        except Exception as e:
            log.error(
                "ollama_classification_failed",
                error=str(e),
                error_type=type(e).__name__,
                message=message[:50],
            )
            # Default to complex task with Claude on unexpected errors
            return RoutingDecision(
                intent=MessageIntent.COMPLEX_TASK,
                confidence=0.5,
                reasoning="Ollama classification failed unexpectedly, defaulting to complex task",
                use_claude=True,
            )

    async def generate_simple_response(self, message: str) -> str:
        """Generate a response for simple queries using Ollama.

        Args:
            message: The user's simple query.

        Returns:
            Generated response.
        """
        try:
            response = await self._client.post(
                f"{self._url}/api/generate",
                json={
                    "model": self._model,
                    "prompt": message,
                    "stream": False,
                    "options": {
                        "temperature": 0.7,
                        "num_predict": 500,
                    },
                },
            )
            response.raise_for_status()

            result_data = response.json()
            return result_data.get("response", "").strip()  # type: ignore[no-any-return]

        except Exception as e:
            log.error("ollama_generation_failed", error=str(e))
            return "I'm having trouble processing that. Could you try again?"

    async def health_check(self) -> bool:
        """Check if Ollama is healthy and the model is available.

        Returns:
            True if healthy, False otherwise.
        """
        try:
            response = await self._client.get(f"{self._url}/api/tags", timeout=5.0)
            response.raise_for_status()

            models = response.json()
            model_names = [m["name"] for m in models.get("models", [])]

            # Check if our configured model is available
            is_healthy = self._model in model_names

            if is_healthy:
                log.info("ollama_health_check_passed", model=self._model)
            else:
                log.warning(
                    "ollama_model_not_found",
                    model=self._model,
                    available_models=model_names,
                )

            return is_healthy

        except Exception as e:
            log.error("ollama_health_check_failed", error=str(e))
            return False

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
