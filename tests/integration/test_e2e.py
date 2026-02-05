"""End-to-end integration tests for SecureClaw.

This test suite starts the entire Docker environment and simulates real user interactions.
"""

import asyncio
import os
import subprocess
import time
from typing import Any

import pytest

# Check if we should run integration tests
SKIP_INTEGRATION = os.getenv("SKIP_INTEGRATION_TESTS", "false").lower() == "true"


class DockerEnvironment:
    """Manages Docker Compose environment for testing."""

    def __init__(self) -> None:
        """Initialize the Docker environment manager."""
        self.compose_file = "docker-compose.yml"
        self.project_name = "secureclaw-test"

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

    def wait_for_healthy(self, timeout: int = 120) -> bool:
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
                # Check if Qdrant is healthy
                result = subprocess.run(
                    [
                        "docker",
                        "exec",
                        f"{self.project_name}-qdrant-1",
                        "curl",
                        "-f",
                        "http://localhost:6333/health",
                    ],
                    capture_output=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    print("✅ Qdrant is healthy")

                    # Check if SecureClaw container is running
                    result = subprocess.run(
                        [
                            "docker",
                            "ps",
                            "--filter",
                            f"name={self.project_name}-secureclaw-1",
                            "--filter",
                            "status=running",
                            "--format",
                            "{{.Names}}",
                        ],
                        capture_output=True,
                        text=True,
                    )
                    if f"{self.project_name}-secureclaw-1" in result.stdout:
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


class MockDiscordBot:
    """Mock Discord bot for testing agent logic without Discord API."""

    def __init__(self) -> None:
        """Initialize the mock bot."""
        # Import here to avoid issues if Discord isn't available
        from secureclaw.agent.core import Agent
        from secureclaw.memory.qdrant import QdrantMemory

        # Initialize components
        self.memory = QdrantMemory()
        self.agent = Agent(memory=self.memory)
        self.test_user_id = 123456789
        self.test_channel_id = 987654321

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
def docker_env() -> Any:
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


@pytest.fixture
async def mock_bot() -> Any:
    """Pytest fixture for mock Discord bot."""
    bot = MockDiscordBot()
    await bot.initialize()
    yield bot
    await bot.cleanup()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_simple_question(mock_bot: MockDiscordBot) -> None:
    """Test simple question handling."""
    response = await mock_bot.simulate_message("What is 2+2?")

    assert response is not None
    assert len(response) > 0
    assert isinstance(response, str)
    print(f"✅ Simple question test passed: {response[:100]}...")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_memory_store_and_recall(mock_bot: MockDiscordBot) -> None:
    """Test memory storage and recall."""
    # Store a memory
    store_response = await mock_bot.simulate_message("Remember that my favorite color is blue")
    assert "remember" in store_response.lower() or "blue" in store_response.lower()
    print(f"✅ Memory stored: {store_response}")

    # Wait a moment
    await asyncio.sleep(2)

    # Recall the memory
    recall_response = await mock_bot.simulate_message("What is my favorite color?")
    assert "blue" in recall_response.lower()
    print(f"✅ Memory recalled: {recall_response}")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_complex_task(mock_bot: MockDiscordBot) -> None:
    """Test complex task handling."""
    response = await mock_bot.simulate_message(
        "Can you explain the difference between async and sync programming in Python?"
    )

    assert response is not None
    assert len(response) > 50  # Should be a detailed response
    assert any(word in response.lower() for word in ["async", "await", "concurrent", "thread"])
    print(f"✅ Complex task test passed: {response[:100]}...")


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
    assert "testuser" in response2.lower() or "name" in response2.lower()
    print(f"✅ Context retention test passed: {response2}")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_help_command(mock_bot: MockDiscordBot) -> None:
    """Test help command."""
    response = await mock_bot.simulate_message("help")

    assert "secureclaw" in response.lower() or "can" in response.lower()
    assert any(word in response.lower() for word in ["ask", "remember", "search"])
    print(f"✅ Help command test passed: {response[:100]}...")


@pytest.mark.integration
def test_docker_services_running(docker_env: DockerEnvironment) -> None:
    """Test that all Docker services are running."""
    # Check Qdrant
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            f"name={docker_env.project_name}-qdrant-1",
            "--filter",
            "status=running",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
    )
    assert f"{docker_env.project_name}-qdrant-1" in result.stdout
    print("✅ Qdrant container is running")

    # Check SecureClaw
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            f"name={docker_env.project_name}-secureclaw-1",
            "--filter",
            "status=running",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
    )
    assert f"{docker_env.project_name}-secureclaw-1" in result.stdout
    print("✅ SecureClaw container is running")


@pytest.mark.integration
def test_qdrant_collections_exist(docker_env: DockerEnvironment) -> None:
    """Test that Qdrant collections are created."""
    result = subprocess.run(
        [
            "docker",
            "exec",
            f"{docker_env.project_name}-qdrant-1",
            "curl",
            "-s",
            "http://localhost:6333/collections",
        ],
        capture_output=True,
        text=True,
    )

    assert "conversations" in result.stdout or "long_term_memory" in result.stdout
    print("✅ Qdrant collections verified")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "-m", "integration"])
