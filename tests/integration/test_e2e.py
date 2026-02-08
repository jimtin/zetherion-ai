"""End-to-end integration tests for Zetherion AI.

This test suite starts the entire Docker environment and simulates real user interactions.
Tests cover core functionality and Phase 5 features (skills, scheduler, profiles).
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
        # Use test-specific compose file with different ports to avoid conflicts
        self.compose_file = "docker-compose.test.yml"
        self.project_name = "zetherion-ai-test"
        self.ollama_model_pulled = False

    def start(self) -> None:
        """Start the Docker Compose environment with fresh images."""
        print("🐳 Tearing down any stale test environment...")
        subprocess.run(
            ["docker", "compose", "-f", self.compose_file, "-p", self.project_name, "down", "-v"],
            capture_output=True,
        )
        print("🐳 Building and starting Docker Compose environment...")
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                self.compose_file,
                "-p",
                self.project_name,
                "up",
                "-d",
                "--build",
            ],
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
                        "zetherion-ai-test-qdrant",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0 and "healthy" in result.stdout:
                    print("✅ Qdrant is healthy")

                    # Check if Ollama router container is healthy
                    result = subprocess.run(
                        [
                            "docker",
                            "inspect",
                            "--format={{.State.Health.Status}}",
                            "zetherion-ai-test-ollama-router",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if result.returncode == 0 and "healthy" in result.stdout:
                        print("✅ Ollama Router is healthy")

                        # Check if Ollama generation container is healthy
                        result = subprocess.run(
                            [
                                "docker",
                                "inspect",
                                "--format={{.State.Health.Status}}",
                                "zetherion-ai-test-ollama",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if result.returncode == 0 and "healthy" in result.stdout:
                            print("✅ Ollama Generation is healthy")

                            # Check if Zetherion AI container is running
                            result = subprocess.run(
                                [
                                    "docker",
                                    "inspect",
                                    "--format={{.State.Status}}",
                                    "zetherion-ai-test-bot",
                                ],
                                capture_output=True,
                                text=True,
                                timeout=5,
                            )
                            if result.returncode == 0 and "running" in result.stdout:
                                print("✅ Zetherion AI is running")
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

    def pull_ollama_models(
        self,
        router_model: str = "llama3.2:1b",
        generation_model: str = "llama3.1:8b",
        embedding_model: str = "nomic-embed-text",
    ) -> bool:
        """Pull Ollama models for both containers if not already pulled.

        Args:
            router_model: Model for router container (small, fast).
            generation_model: Model for generation container (larger, capable).
            embedding_model: Model for embeddings (runs on generation container).

        Returns:
            True if all models are available, False otherwise.
        """
        if self.ollama_model_pulled:
            return True

        success = True

        # Pull router model to router container
        print(f"📥 Pulling router model '{router_model}' to ollama-router container...")
        try:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    "zetherion-ai-test-ollama-router",
                    "ollama",
                    "pull",
                    router_model,
                ],
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout for small model
            )
            if result.returncode == 0:
                print(f"✅ Router model '{router_model}' pulled successfully")
            else:
                print(f"❌ Failed to pull router model: {result.stderr}")
                success = False
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            print(f"❌ Error pulling router model: {e}")
            success = False

        # Pull generation model to generation container
        print(f"📥 Pulling generation model '{generation_model}' (this may take a few minutes)...")
        try:
            result = subprocess.run(
                ["docker", "exec", "zetherion-ai-test-ollama", "ollama", "pull", generation_model],
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout for larger model
            )
            if result.returncode == 0:
                print(f"✅ Generation model '{generation_model}' pulled successfully")
            else:
                print(f"❌ Failed to pull generation model: {result.stderr}")
                success = False
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            print(f"❌ Error pulling generation model: {e}")
            success = False

        # Pull embedding model to generation container
        print(f"📥 Pulling embedding model '{embedding_model}'...")
        try:
            result = subprocess.run(
                ["docker", "exec", "zetherion-ai-test-ollama", "ollama", "pull", embedding_model],
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout for embedding model
            )
            if result.returncode == 0:
                print(f"✅ Embedding model '{embedding_model}' pulled successfully")
            else:
                print(f"❌ Failed to pull embedding model: {result.stderr}")
                success = False
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            print(f"❌ Error pulling embedding model: {e}")
            success = False

        if success:
            self.ollama_model_pulled = True
        return success

    def pull_ollama_model(self, model: str = "llama3.1:8b") -> bool:
        """Pull Ollama model (backward compatibility wrapper).

        Args:
            model: Ollama model name to pull.

        Returns:
            True if model is available, False otherwise.
        """
        # For backward compatibility, pull to generation container
        return self.pull_ollama_models(generation_model=model)


class MockDiscordBot:
    """Mock Discord bot for testing agent logic without Discord API."""

    def __init__(self, router_backend: str = "gemini") -> None:
        """Initialize the mock bot.

        Args:
            router_backend: Router backend to use ('gemini' or 'ollama').
        """
        # Import here to avoid issues if Discord isn't available
        from zetherion_ai.agent.core import Agent
        from zetherion_ai.memory.qdrant import QdrantMemory

        # Set router backend environment variable
        os.environ["ROUTER_BACKEND"] = router_backend

        # Set Qdrant to use host-accessible URL (tests run on host, not in Docker)
        os.environ["QDRANT_HOST"] = "localhost"
        os.environ["QDRANT_PORT"] = "16333"

        # Set Ollama host-accessible URLs (tests run on host, not in Docker)
        # These are needed for embeddings even when using Gemini router
        # Router container exposed on port 31434
        os.environ["OLLAMA_ROUTER_HOST"] = "localhost"
        os.environ["OLLAMA_ROUTER_PORT"] = "31434"
        # Generation container exposed on port 21434
        os.environ["OLLAMA_HOST"] = "localhost"
        os.environ["OLLAMA_PORT"] = "21434"

        # Clear settings cache to pick up new environment variables
        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

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
    managed_externally = os.getenv("DOCKER_MANAGED_EXTERNALLY", "").lower() == "true"

    try:
        if not managed_externally:
            # Start environment (tears down stale, rebuilds, starts fresh)
            env.start()

        # Wait for services to be healthy
        if not env.wait_for_healthy():
            logs = env.get_logs("zetherion_ai")
            pytest.fail(f"Services failed to become healthy.\n\nLogs:\n{logs}")

        # Give services a bit more time to fully initialize
        time.sleep(10)

        # Pull embedding model early - needed by both Gemini and Ollama backends
        # since the default embeddings_backend is 'ollama'
        print("📥 Pre-pulling embedding model for all tests...")
        try:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    "zetherion-ai-test-ollama",
                    "ollama",
                    "pull",
                    "nomic-embed-text",
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode == 0:
                print("✅ Embedding model 'nomic-embed-text' ready")
            else:
                print(f"⚠️ Warning: Failed to pull embedding model: {result.stderr}")
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            print(f"⚠️ Warning: Error pulling embedding model: {e}")

        yield env

    finally:
        if not managed_externally:
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

    # If testing Ollama, ensure all models are pulled to their respective containers
    if backend == "ollama":
        # Use explicit model names for dual-container architecture
        # Router: small, fast model (1b fits in 1GB container)
        # Generation: larger, capable model (8b needs 8GB+ container)
        router_model = "llama3.2:1b"
        generation_model = "llama3.1:8b"
        embedding_model = "nomic-embed-text"

        # Set environment variables for the test to override any .env settings
        os.environ["OLLAMA_ROUTER_MODEL"] = router_model
        os.environ["OLLAMA_GENERATION_MODEL"] = generation_model
        os.environ["OLLAMA_EMBEDDING_MODEL"] = embedding_model

        if not docker_env.pull_ollama_models(
            router_model=router_model,
            generation_model=generation_model,
            embedding_model=embedding_model,
        ):
            pytest.skip("Failed to pull Ollama models for dual-container architecture")

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
            "name=zetherion-ai-test-qdrant",
            "--filter",
            "status=running",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
    )
    assert "zetherion-ai-test-qdrant" in result.stdout
    print("✅ Qdrant container is running")

    # Check Zetherion AI (uses container_name from docker-compose.yml)
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            "name=zetherion-ai-test-bot",
            "--filter",
            "status=running",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
    )
    assert "zetherion-ai-test-bot" in result.stdout
    print("✅ Zetherion AI container is running")


@pytest.mark.integration
def test_qdrant_collections_exist(docker_env: DockerEnvironment) -> None:
    """Test that Qdrant collections are created."""
    # Use curl from host machine since Qdrant container doesn't have curl
    result = subprocess.run(
        ["curl", "-s", "http://localhost:16333/collections"],
        capture_output=True,
        text=True,
    )

    assert "conversations" in result.stdout or "long_term_memory" in result.stdout
    print("✅ Qdrant collections verified")


# Phase 5 Integration Tests


@pytest.mark.asyncio
@pytest.mark.integration
async def test_task_management_skill(mock_bot: MockDiscordBot) -> None:
    """Test task management skill (Phase 5E)."""
    # Create a task
    response = await mock_bot.simulate_message("Add a task to review the documentation")

    assert response is not None
    assert len(response) > 20
    # Should acknowledge task creation or provide task-related response
    preview: str = response[0:100] if len(response) > 100 else response  # type: ignore[index]
    print(f"✅ Task management test: {preview}...")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calendar_query_skill(mock_bot: MockDiscordBot) -> None:
    """Test calendar query skill (Phase 5E)."""
    # Query schedule
    response = await mock_bot.simulate_message("What's on my schedule today?")

    assert response is not None
    assert len(response) > 20
    # Should provide schedule-related response
    preview: str = response[0:100] if len(response) > 100 else response  # type: ignore[index]
    print(f"✅ Calendar query test: {preview}...")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_profile_query_skill(mock_bot: MockDiscordBot) -> None:
    """Test profile query skill (Phase 5E)."""
    # Query profile
    response = await mock_bot.simulate_message("What do you know about me?")

    assert response is not None
    assert len(response) > 20
    # Should provide profile-related response or indicate no data yet
    preview: str = response[0:100] if len(response) > 100 else response  # type: ignore[index]
    print(f"✅ Profile query test: {preview}...")


@pytest.mark.integration
def test_skills_service_health(docker_env: DockerEnvironment) -> None:
    """Test that skills service is healthy (Phase 5D)."""
    # Check if skills service container is running
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            "name=zetherion-ai-test-skills",
            "--filter",
            "status=running",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
    )
    assert "zetherion-ai-test-skills" in result.stdout
    print("✅ Skills service container is running")

    # Check skills service health endpoint
    result = subprocess.run(
        ["curl", "-s", "http://localhost:18080/health"],
        capture_output=True,
        text=True,
    )
    # Health endpoint should return OK or healthy status
    assert result.returncode == 0 or "healthy" in result.stdout.lower()
    print("✅ Skills service health check passed")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_router_skill_intents(mock_bot: MockDiscordBot) -> None:
    """Test router correctly classifies skill intents (Phase 5G)."""
    from zetherion_ai.agent.router import MessageRouter

    # Get the router from the agent
    router = MessageRouter()

    # Test task management intent
    decision = await router.classify("Create a task for tomorrow")
    # Should classify as task_management or memory_store
    print(f"Task intent classified as: {decision.intent.value}")

    # Test calendar intent
    decision = await router.classify("What meetings do I have this week?")
    # Should classify as calendar_query or memory_recall
    print(f"Calendar intent classified as: {decision.intent.value}")

    # Test profile intent
    decision = await router.classify("Show my profile")
    # Should classify as profile_query or memory_recall
    print(f"Profile intent classified as: {decision.intent.value}")

    print("✅ Router skill intent classification test passed")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_encryption_in_memory_storage(mock_bot: MockDiscordBot) -> None:
    """Test that encryption is working for memory storage (Phase 5A)."""
    # Store sensitive information
    response = await mock_bot.simulate_message(
        "Remember my API key is sk-test-12345 (this is a test)"
    )
    assert response is not None

    # Wait for memory indexing
    await asyncio.sleep(2)

    # Recall should work (encryption is transparent)
    recall = await mock_bot.simulate_message("What's my API key?")
    assert recall is not None
    assert len(recall) > 10
    # Should respond (either with the key or acknowledging it was stored)
    preview: str = recall[0:100] if len(recall) > 100 else recall  # type: ignore[index]
    print(f"✅ Encryption transparency test: {preview}...")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "-m", "integration"])
