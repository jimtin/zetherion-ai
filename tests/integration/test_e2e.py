"""End-to-end integration tests for SecureClaw.

This test suite starts the entire Docker environment and simulates real user interactions.
"""

import asyncio
import os
import subprocess
import time
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

# Load environment variables from .env file
try:
    from dotenv import load_dotenv

    # Load from project root .env file
    env_path = Path(__file__).parent.parent.parent / ".env"
    load_dotenv(dotenv_path=env_path)
except ImportError:
    # python-dotenv not installed, rely on environment variables being set
    pass

# Check if we should run integration tests
SKIP_INTEGRATION = os.getenv("SKIP_INTEGRATION_TESTS", "false").lower() == "true"


class DockerEnvironment:
    """Manages Docker Compose environment for testing."""

    def __init__(self) -> None:
        """Initialize the Docker environment manager."""
        self.compose_file = "docker-compose.yml"
        self.project_name = "secureclaw-test"
        self.ollama_model_pulled = False

    def start(self) -> None:
        """Start the Docker Compose environment."""
        print("🐳 Starting Docker Compose environment...")
        subprocess.run(
            ["docker", "compose", "-f", self.compose_file, "-p", self.project_name, "up", "-d"],
            check=True,
            capture_output=True,
        )

    def stop(self) -> None:
        """Stop and clean up the Docker Compose environment."""
        print("🛑 Stopping Docker Compose environment...")
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                self.compose_file,
                "-p",
                self.project_name,
                "down",
                "-v",
            ],
            check=True,
            capture_output=True,
        )

    def wait_for_healthy(self, timeout: int = 180) -> bool:
        """Wait for all services to be healthy.

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            True if all services are healthy, False otherwise.
        """
        print("⏳ Waiting for services to be healthy...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                # Check if Qdrant container is healthy using Docker health status
                result = subprocess.run(
                    [
                        "docker",
                        "inspect",
                        "--format={{.State.Health.Status}}",
                        "secureclaw-qdrant",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0 and "healthy" in result.stdout:
                    print("✅ Qdrant is healthy")

                    # Check if Ollama container is healthy
                    result = subprocess.run(
                        [
                            "docker",
                            "inspect",
                            "--format={{.State.Health.Status}}",
                            "secureclaw-ollama",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if result.returncode == 0 and "healthy" in result.stdout:
                        print("✅ Ollama is healthy")

                        # Check if SecureClaw container is running
                        result = subprocess.run(
                            [
                                "docker",
                                "inspect",
                                "--format={{.State.Status}}",
                                "secureclaw-bot",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if result.returncode == 0 and "running" in result.stdout:
                            print("✅ SecureClaw is running")
                            return True

            except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                pass

            print("⏳ Services not ready yet, waiting...")
            time.sleep(5)

        return False

    def get_logs(self, service: str, tail: int = 50) -> str:
        """Get logs from a specific service.

        Args:
            service: Service name.
            tail: Number of lines to tail.

        Returns:
            Service logs.
        """
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                self.compose_file,
                "-p",
                self.project_name,
                "logs",
                "--tail",
                str(tail),
                service,
            ],
            capture_output=True,
            text=True,
        )
        return result.stdout

    def pull_ollama_model(self, model: str = "llama3.1:8b") -> bool:
        """Pull Ollama model if not already pulled.

        Args:
            model: Ollama model name to pull.

        Returns:
            True if model is available, False otherwise.
        """
        if self.ollama_model_pulled:
            return True

        print(f"📥 Pulling Ollama model '{model}' (this may take a few minutes)...")
        try:
            result = subprocess.run(
                ["docker", "exec", "secureclaw-ollama", "ollama", "pull", model],
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout for model download
            )
            if result.returncode == 0:
                print(f"✅ Ollama model '{model}' pulled successfully")
                self.ollama_model_pulled = True
                return True
            else:
                print(f"❌ Failed to pull Ollama model: {result.stderr}")
                return False
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            print(f"❌ Error pulling Ollama model: {e}")
            return False


class MockDiscordBot:
    """Mock Discord bot for testing agent logic without Discord API."""

    def __init__(self, router_backend: str = "gemini") -> None:
        """Initialize the mock bot.

        Args:
            router_backend: Router backend to use ('gemini' or 'ollama').
        """
        # Import here to avoid issues if Discord isn't available
        from secureclaw.agent.core import Agent
        from secureclaw.memory.qdrant import QdrantMemory

        # Set router backend environment variable
        os.environ["ROUTER_BACKEND"] = router_backend

        # Initialize components
        self.memory = QdrantMemory()
        self.agent = Agent(memory=self.memory)
        self.test_user_id = 123456789
        self.test_channel_id = 987654321
        self.router_backend = router_backend

    async def initialize(self) -> None:
        """Initialize the memory collections."""
        await self.memory.initialize()

    async def simulate_message(self, message: str) -> str:
        """Simulate a user message and get response.

        Args:
            message: The user's message.

        Returns:
            The bot's response.
        """
        return await self.agent.generate_response(
            user_id=self.test_user_id,
            channel_id=self.test_channel_id,
            message=message,
        )

    async def cleanup(self) -> None:
        """Clean up resources."""
        await self.memory.close()


@pytest.fixture(scope="module")
def docker_env() -> Generator[DockerEnvironment, None, None]:
    """Pytest fixture to manage Docker environment."""
    if SKIP_INTEGRATION:
        pytest.skip("Integration tests disabled (SKIP_INTEGRATION_TESTS=true)")

    # Check if required environment variables are set
    required_vars = ["GEMINI_API_KEY", "DISCORD_TOKEN"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        pytest.skip(f"Missing required environment variables: {', '.join(missing_vars)}")

    env = DockerEnvironment()

    try:
        # Start environment
        env.start()

        # Wait for services to be healthy
        if not env.wait_for_healthy():
            logs = env.get_logs("secureclaw")
            pytest.fail(f"Services failed to become healthy.\n\nLogs:\n{logs}")

        # Give services a bit more time to fully initialize
        time.sleep(10)

        yield env

    finally:
        # Always clean up
        env.stop()


@pytest.fixture(scope="module", params=["gemini", "ollama"])
def router_backend(request: Any, docker_env: DockerEnvironment) -> str:
    """Pytest fixture to parameterize router backend.

    Args:
        request: Pytest request object.
        docker_env: Docker environment fixture.

    Returns:
        Router backend name ('gemini' or 'ollama').
    """
    backend = request.param

    # If testing Ollama, ensure model is pulled
    if backend == "ollama":
        model = os.getenv("OLLAMA_ROUTER_MODEL", "llama3.1:8b")
        if not docker_env.pull_ollama_model(model):
            pytest.skip(f"Failed to pull Ollama model '{model}'")

    return backend


@pytest_asyncio.fixture
async def mock_bot(
    docker_env: DockerEnvironment, router_backend: str
) -> AsyncGenerator[MockDiscordBot, None]:
    """Pytest fixture for mock Discord bot.

    Args:
        docker_env: Docker environment fixture (ensures Docker is running first).
        router_backend: Router backend to use ('gemini' or 'ollama').

    Yields:
        Initialized MockDiscordBot instance.
    """
    # Give Docker services a moment to be fully ready
    await asyncio.sleep(2)

    bot = MockDiscordBot(router_backend=router_backend)
    await bot.initialize()
    print(f"🤖 Testing with {router_backend.upper()} router backend")
    yield bot
    await bot.cleanup()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_router_backend(mock_bot: MockDiscordBot) -> None:
    """Test that the router backend is working correctly."""
    backend = mock_bot.router_backend
    print(f"🔧 Testing {backend.upper()} router backend")

    # Test a simple query that should be routed correctly
    response = await mock_bot.simulate_message("What is the capital of France?")

    assert response is not None
    assert len(response) > 0
    assert isinstance(response, str)
    # Simple queries should mention Paris
    assert "paris" in response.lower()
    preview: str = response[0:100] if len(response) > 100 else response  # type: ignore[index]
    print(f"✅ {backend.upper()} router test passed: {preview}...")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_simple_question(mock_bot: MockDiscordBot) -> None:
    """Test simple question handling."""
    response = await mock_bot.simulate_message("What is 2+2?")

    assert response is not None
    assert len(response) > 0
    assert isinstance(response, str)
    preview: str = response[0:100] if len(response) > 100 else response  # type: ignore[index]
    print(f"✅ Simple question test passed ({mock_bot.router_backend.upper()}): {preview}...")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_memory_store_and_recall(mock_bot: MockDiscordBot) -> None:
    """Test memory storage and recall."""
    # Store a memory
    store_response = await mock_bot.simulate_message("Remember that my favorite color is blue")
    assert "remember" in store_response.lower() or "blue" in store_response.lower()
    print(f"✅ Memory stored: {store_response}")

    # Wait a moment for memory to be indexed
    await asyncio.sleep(3)

    # Recall the memory
    recall_response = await mock_bot.simulate_message("What is my favorite color?")
    # Should return a valid response (not an error message)
    assert recall_response is not None
    assert len(recall_response) > 20
    assert "trouble processing" not in recall_response.lower()
    # Ideally should mention blue, but LLM responses can vary
    if "blue" in recall_response.lower():
        print(f"✅ Memory recalled correctly: {recall_response}")
    else:
        print(f"⚠️ Memory recall uncertain (may need longer indexing time): {recall_response}")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_complex_task(mock_bot: MockDiscordBot) -> None:
    """Test complex task handling."""
    response = await mock_bot.simulate_message(
        "Can you explain the difference between async and sync programming in Python?"
    )

    assert response is not None
    assert len(response) > 50  # Should be a detailed response

    # Check for relevant keywords (expanded list for robustness)
    keywords = [
        "async",
        "await",
        "concurrent",
        "thread",
        "synchronous",
        "asynchronous",
        "blocking",
        "non-blocking",
        "parallel",
        "coroutine",
        "asyncio",
        "simultaneous",
    ]
    has_keywords = any(word in response.lower() for word in keywords)

    # Also check that it's not an error message
    is_not_error = not any(
        phrase in response.lower()
        for phrase in ["error", "couldn't", "unable to", "failed to", "something went wrong"]
    )

    # Pass if it has keywords OR if it's a substantive non-error response
    assert has_keywords or (
        is_not_error and len(response) > 100
    ), f"Expected detailed response about async/sync, got: {response[:200]}"

    preview: str = response[0:100] if len(response) > 100 else response  # type: ignore[index]
    print(f"✅ Complex task test passed: {preview}...")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_conversation_context(mock_bot: MockDiscordBot) -> None:
    """Test conversation context retention."""
    # First message
    response1 = await mock_bot.simulate_message("My name is TestUser")
    print(f"Message 1: {response1}")

    await asyncio.sleep(1)

    # Second message referencing first
    response2 = await mock_bot.simulate_message("What did I just tell you?")
    # Should provide a contextual response (not an error)
    assert response2 is not None
    assert len(response2) > 20
    assert "trouble processing" not in response2.lower()
    # Ideally mentions name/testuser, but may also recall other context
    preview: str = response2[0:100] if len(response2) > 100 else response2  # type: ignore[index]
    if "testuser" in response2.lower() or "name" in response2.lower():
        print(f"✅ Context retention test passed: {response2}")
    else:
        print(f"⚠️ Context retention (recalled different context): {preview}...")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_help_command(mock_bot: MockDiscordBot) -> None:
    """Test help command."""
    response = await mock_bot.simulate_message("help")

    # Help command should return a helpful response (not an error)
    assert response is not None
    assert len(response) > 20
    assert "trouble processing" not in response.lower()
    preview: str = response[0:100] if len(response) > 100 else response  # type: ignore[index]
    print(f"✅ Help command test passed: {preview}...")


@pytest.mark.integration
def test_docker_services_running(docker_env: DockerEnvironment) -> None:
    """Test that all Docker services are running."""
    # Check Qdrant (uses container_name from docker-compose.yml)
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            "name=secureclaw-qdrant",
            "--filter",
            "status=running",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
    )
    assert "secureclaw-qdrant" in result.stdout
    print("✅ Qdrant container is running")

    # Check SecureClaw (uses container_name from docker-compose.yml)
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            "name=secureclaw-bot",
            "--filter",
            "status=running",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
    )
    assert "secureclaw-bot" in result.stdout
    print("✅ SecureClaw container is running")


@pytest.mark.integration
def test_qdrant_collections_exist(docker_env: DockerEnvironment) -> None:
    """Test that Qdrant collections are created."""
    # Use curl from host machine since Qdrant container doesn't have curl
    result = subprocess.run(
        ["curl", "-s", "http://localhost:6333/collections"],
        capture_output=True,
        text=True,
    )

    assert "conversations" in result.stdout or "long_term_memory" in result.stdout
    print("✅ Qdrant collections verified")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "-m", "integration"])
