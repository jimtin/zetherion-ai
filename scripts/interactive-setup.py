#!/usr/bin/env python3
"""Interactive setup script for Zetherion AI.

This script guides users through first-time configuration:
- Prompts for required API keys
- Validates key formats
- Suggests router backend
- Recommends Ollama model based on hardware
- Generates .env file
"""

import json
import re
import subprocess
import sys
from pathlib import Path


class Colors:
    """ANSI color codes for terminal output."""

    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    END = "\033[0m"


def print_header(text: str) -> None:
    """Print a formatted header."""
    print(f"\n{Colors.BLUE}{Colors.BOLD}{'=' * 60}{Colors.END}")
    print(f"{Colors.BLUE}{Colors.BOLD}{text:^60}{Colors.END}")
    print(f"{Colors.BLUE}{Colors.BOLD}{'=' * 60}{Colors.END}\n")


def print_success(text: str) -> None:
    """Print success message."""
    print(f"{Colors.GREEN}✓ {text}{Colors.END}")


def print_warning(text: str) -> None:
    """Print warning message."""
    print(f"{Colors.YELLOW}⚠ {text}{Colors.END}")


def print_error(text: str) -> None:
    """Print error message."""
    print(f"{Colors.RED}✗ {text}{Colors.END}")


def print_info(text: str) -> None:
    """Print info message."""
    print(f"{Colors.CYAN}ℹ {text}{Colors.END}")


def validate_discord_token(token: str) -> bool:
    """Validate Discord bot token format.

    Format: Usually starts with MT, Mz, or Nz followed by base64-like characters
    Example: MTQ2ODc4MDQxODY1MTI2MzEyOQ.GGFum2.lsf...
    """
    if not token:
        return False
    # Discord tokens are typically 59+ characters with dots separating parts
    if len(token) < 50:
        return False
    # Should contain dots and alphanumeric characters
    return bool(re.match(r"^[A-Za-z0-9_\-\.]+$", token))


def validate_gemini_key(key: str) -> bool:
    """Validate Gemini API key format.

    Format: Starts with AIzaSy followed by 33 characters
    Example: AIzaSyCO9WodgUFJfW-7qK4Vtbnc...
    """
    if not key:
        return False
    return bool(re.match(r"^AIzaSy[A-Za-z0-9_\-]{33}$", key))


def validate_anthropic_key(key: str) -> bool:
    """Validate Anthropic API key format.

    Format: Starts with sk-ant-api03- followed by base58 characters
    Example: sk-ant-api03-OEKnlIipBFzx...
    """
    if not key:
        return False
    return bool(re.match(r"^sk-ant-api\d{2}-[A-Za-z0-9_\-]{95,}$", key))


def validate_openai_key(key: str) -> bool:
    """Validate OpenAI API key format.

    Format: Starts with sk- followed by 48 characters
    Example: sk-proj-1234567890abcdef...
    """
    if not key:
        return False
    return bool(re.match(r"^sk-(proj-)?[A-Za-z0-9]{48,}$", key))


def prompt_input(
    prompt: str, required: bool = False, validator: callable | None = None, mask: bool = False
) -> str:
    """Prompt user for input with optional validation.

    Args:
        prompt: The prompt text to display
        required: Whether the field is required
        validator: Optional validation function
        mask: Whether to mask input (for sensitive data)

    Returns:
        User input string
    """
    while True:
        if mask:
            # Note: In production, use getpass.getpass() but it doesn't work well in some terminals
            print(f"{prompt}: ", end="", flush=True)
            value = input()
        else:
            value = input(f"{prompt}: ").strip()

        if not value:
            if required:
                print_error("This field is required. Please enter a value.")
                continue
            return ""

        if validator and not validator(value):
            print_error("Invalid format. Please check your input and try again.")
            continue

        return value


def prompt_yes_no(prompt: str, default: bool = True) -> bool:
    """Prompt user for yes/no input.

    Args:
        prompt: The prompt text to display
        default: Default value if user presses Enter

    Returns:
        True for yes, False for no
    """
    default_str = "Y/n" if default else "y/N"
    while True:
        response = input(f"{prompt} [{default_str}]: ").strip().lower()

        if not response:
            return default

        if response in ["y", "yes"]:
            return True
        elif response in ["n", "no"]:
            return False
        else:
            print_error("Please enter 'y' or 'n'")


def get_hardware_assessment() -> dict | None:
    """Run hardware assessment and return recommendations.

    Returns:
        Hardware assessment dict or None if failed
    """
    print_info("Assessing your system hardware...")

    try:
        # Build the assessment container if needed
        result = subprocess.run(
            [
                "docker",
                "build",
                "-t",
                "zetherion-ai-assess:distroless",
                "-f",
                "Dockerfile.assess",
                ".",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            print_warning("Failed to build hardware assessment container")
            return None

        # Run assessment with JSON output
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "/usr/bin/python3.11",
                "zetherion-ai-assess:distroless",
                "/app/assess-system.py",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            print_warning("Hardware assessment failed, using defaults")
            return None

        # Parse JSON output
        assessment = json.loads(result.stdout)
        return assessment

    except subprocess.TimeoutExpired:
        print_warning("Hardware assessment timed out")
        return None
    except json.JSONDecodeError:
        print_warning("Failed to parse hardware assessment output")
        return None
    except Exception as e:
        print_warning(f"Hardware assessment error: {e}")
        return None


def display_hardware_info(assessment: dict) -> None:
    """Display hardware assessment information."""
    hw = assessment.get("hardware", {})
    rec = assessment.get("recommendation", {})

    print(f"\n{Colors.BOLD}System Hardware:{Colors.END}")
    print(f"  CPU: {hw.get('cpu_model', 'Unknown')}")
    if hw.get("cpu_count"):
        print(f"  Cores: {hw['cpu_count']} ({hw.get('cpu_threads', '?')} threads)")
    if hw.get("ram_gb"):
        print(f"  RAM: {hw['ram_gb']} GB total, {hw.get('available_ram_gb', '?')} GB available")
    gpu = hw.get("gpu", {})
    print(f"  GPU: {gpu.get('name', 'Unknown')}")

    print(f"\n{Colors.BOLD}Recommended Model:{Colors.END}")
    print(f"  Model: {Colors.GREEN}{rec.get('model', 'llama3.1:8b')}{Colors.END}")
    print(f"  Size: {rec.get('size_gb', 4.7)} GB download")
    print(f"  RAM Required: {rec.get('ram_required', 8)} GB minimum")
    print(f"  Quality: {rec.get('quality', 'Excellent')}")
    print(f"  Speed: {rec.get('inference_time', '~2-3s')}")
    print(f"  Reason: {rec.get('reason', 'Balanced performance')}")

    warnings = assessment.get("warnings", [])
    if warnings:
        print(f"\n{Colors.YELLOW}Warnings:{Colors.END}")
        for warning in warnings:
            print(f"  ⚠ {warning}")


def generate_env_file(config: dict) -> bool:
    """Generate .env file from configuration.

    Args:
        config: Configuration dictionary

    Returns:
        True if successful, False otherwise
    """
    env_example = Path(".env.example")
    env_file = Path(".env")

    if not env_example.exists():
        print_error(".env.example not found")
        return False

    # Read .env.example
    lines = env_example.read_text().splitlines()

    # Update values
    updated_lines = []
    for line in lines:
        # Required keys
        if line.startswith("DISCORD_TOKEN="):
            updated_lines.append(f"DISCORD_TOKEN={config['discord_token']}")
        elif line.startswith("GEMINI_API_KEY="):
            updated_lines.append(f"GEMINI_API_KEY={config['gemini_key']}")
        # Optional keys
        elif line.startswith("ANTHROPIC_API_KEY="):
            value = config.get("anthropic_key", "")
            updated_lines.append(f"ANTHROPIC_API_KEY={value}")
        elif line.startswith("OPENAI_API_KEY="):
            value = config.get("openai_key", "")
            updated_lines.append(f"OPENAI_API_KEY={value}")
        # Router backend
        elif line.startswith("ROUTER_BACKEND="):
            updated_lines.append(f"ROUTER_BACKEND={config['router_backend']}")
        # Ollama models
        elif line.startswith("OLLAMA_ROUTER_MODEL="):
            updated_lines.append(line)  # Keep default router model
        elif line.startswith("OLLAMA_GENERATION_MODEL="):
            if config["router_backend"] == "ollama":
                updated_lines.append(
                    f"OLLAMA_GENERATION_MODEL={config.get('ollama_model', 'llama3.1:8b')}"
                )
            else:
                updated_lines.append(line)
        # Docker memory
        elif line.startswith("OLLAMA_DOCKER_MEMORY="):
            if config["router_backend"] == "ollama":
                updated_lines.append(f"OLLAMA_DOCKER_MEMORY={config.get('docker_memory', 8)}")
            else:
                updated_lines.append(line)  # Keep default
        # Encryption (mandatory)
        elif line.startswith("ENCRYPTION_PASSPHRASE="):
            updated_lines.append(f"ENCRYPTION_PASSPHRASE={config.get('encryption_passphrase', '')}")
        else:
            updated_lines.append(line)

    # Write .env file
    env_file.write_text("\n".join(updated_lines) + "\n")
    return True


def main() -> int:
    """Main interactive setup function."""
    print_header("Zetherion AI - Interactive Setup")

    # Check if .env already exists
    env_file = Path(".env")
    if env_file.exists():
        print_warning(".env file already exists")
        if not prompt_yes_no("Overwrite existing configuration?", default=False):
            print_info("Setup cancelled")
            return 0

    config = {}

    # Step 1: Required API Keys
    print(f"\n{Colors.BOLD}Step 1: Required API Keys{Colors.END}")
    print("These keys are required for Zetherion AI to function.\n")

    print_info("Discord Bot Token")
    print("  Get from: https://discord.com/developers/applications")
    config["discord_token"] = prompt_input(
        "Enter your Discord bot token", required=True, validator=validate_discord_token, mask=True
    )

    print_info("\nGemini API Key (for embeddings)")
    print("  Get from: https://aistudio.google.com/app/apikey")
    config["gemini_key"] = prompt_input(
        "Enter your Gemini API key", required=True, validator=validate_gemini_key, mask=True
    )

    # Step 2: Optional API Keys
    print(f"\n{Colors.BOLD}Step 2: Optional API Keys{Colors.END}")
    print("These keys are optional but provide additional capabilities.\n")

    if prompt_yes_no("Add Anthropic (Claude) API key?", default=False):
        print_info("Anthropic API Key")
        print("  Get from: https://console.anthropic.com/")
        config["anthropic_key"] = prompt_input(
            "Enter your Anthropic API key",
            required=False,
            validator=validate_anthropic_key,
            mask=True,
        )

    if prompt_yes_no("Add OpenAI API key?", default=False):
        print_info("OpenAI API Key")
        print("  Get from: https://platform.openai.com/api-keys")
        config["openai_key"] = prompt_input(
            "Enter your OpenAI API key", required=False, validator=validate_openai_key, mask=True
        )

    # Step 3: Router Backend Selection
    print(f"\n{Colors.BOLD}Step 3: Router Backend{Colors.END}")
    print("The router classifies messages and routes them to the appropriate handler.\n")

    print("Available options:")
    print("  1. Gemini (cloud) - Fast, free tier, minimal resources")
    print("  2. Ollama (local) - Private, self-hosted, ~5GB download\n")

    while True:
        choice = input("Choose router backend [1/2]: ").strip()
        if choice == "1":
            config["router_backend"] = "gemini"
            print_success("Using Gemini for routing")
            break
        elif choice == "2":
            config["router_backend"] = "ollama"
            print_success("Using Ollama for routing")

            # Step 4: Hardware Assessment & Model Selection
            print(f"\n{Colors.BOLD}Step 4: Ollama Model Selection{Colors.END}")

            assessment = get_hardware_assessment()
            if assessment:
                display_hardware_info(assessment)

                rec = assessment.get("recommendation", {})
                recommended_model = rec.get("model", "llama3.1:8b")
                docker_memory = rec.get("docker_memory_gb", 8)

                print()
                if prompt_yes_no(f"Use recommended model '{recommended_model}'?", default=True):
                    config["ollama_model"] = recommended_model
                    config["docker_memory"] = docker_memory
                else:
                    print("\nAlternative models:")
                    print("  - phi3:mini (2.3GB, fastest, basic quality)")
                    print("  - mistral:7b (4.1GB, fast, very good quality)")
                    print("  - llama3.1:8b (4.7GB, balanced, excellent quality)")
                    print("  - qwen2.5:7b (4.7GB, best quality, slower)")

                    custom_model = input("\nEnter model name: ").strip()
                    config["ollama_model"] = custom_model if custom_model else recommended_model
                    config["docker_memory"] = docker_memory
            else:
                # Default to balanced model
                config["ollama_model"] = "llama3.1:8b"
                config["docker_memory"] = 8
                print_warning("Using default model: llama3.1:8b")

            break
        else:
            print_error("Please enter 1 or 2")

    # Step: Encryption Configuration (mandatory)
    step_num = "5" if config["router_backend"] == "ollama" else "4"
    print(f"\n{Colors.BOLD}Step {step_num}: Encryption{Colors.END}")
    print("Zetherion AI requires encryption for all stored data.")
    print("You must set a passphrase (minimum 16 characters).\n")

    def validate_passphrase(p: str) -> bool:
        return len(p) >= 16

    config["encryption_passphrase"] = prompt_input(
        "Enter encryption passphrase (min 16 chars)",
        required=True,
        validator=validate_passphrase,
        mask=True,
    )
    print_success("Encryption passphrase set")

    # Generate .env file
    gen_step = "6" if config["router_backend"] == "ollama" else "5"
    print(f"\n{Colors.BOLD}Step {gen_step}: Generating Configuration{Colors.END}")

    if generate_env_file(config):
        print_success(".env file created successfully!")

        print(f"\n{Colors.GREEN}{Colors.BOLD}Setup Complete!{Colors.END}")
        print("\nNext steps:")
        print("  1. Start Zetherion AI: ./start.ps1 (Windows) or ./start.sh (Mac/Linux)")
        print("  2. Invite bot to Discord: Check Discord Developer Portal")
        print("  3. View logs: docker-compose logs -f")

        return 0
    else:
        print_error("Failed to generate .env file")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}Setup cancelled by user{Colors.END}")
        sys.exit(130)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        sys.exit(1)
