#!/usr/bin/env python3
"""System assessment tool for Ollama model recommendations."""

import platform
import subprocess  # nosec B404 - System tools only
import sys
from pathlib import Path


def detect_hardware():
    """Detect system hardware capabilities."""
    info = {
        "platform": platform.system(),
        "machine": platform.machine(),
    }

    try:
        import psutil

        info["cpu_count"] = psutil.cpu_count(logical=False)
        info["cpu_threads"] = psutil.cpu_count(logical=True)
        info["ram_gb"] = round(psutil.virtual_memory().total / (1024**3), 1)
        info["available_ram_gb"] = round(psutil.virtual_memory().available / (1024**3), 1)
        info["disk_free_gb"] = round(psutil.disk_usage(".").free / (1024**3), 1)
    except ImportError:
        print("Warning: psutil not installed, using basic detection", file=sys.stderr)
        # Fallback to sysctl on macOS
        if platform.system() == "Darwin":
            try:
                # Get RAM
                result = subprocess.run(  # nosec B603 B607
                    ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=2
                )
                if result.returncode == 0:
                    info["ram_gb"] = round(int(result.stdout.strip()) / (1024**3), 1)

                # Get CPU count
                result = subprocess.run(  # nosec B603 B607
                    ["sysctl", "-n", "hw.physicalcpu"], capture_output=True, text=True, timeout=2
                )
                if result.returncode == 0:
                    info["cpu_count"] = int(result.stdout.strip())

                result = subprocess.run(  # nosec B603 B607
                    ["sysctl", "-n", "hw.logicalcpu"], capture_output=True, text=True, timeout=2
                )
                if result.returncode == 0:
                    info["cpu_threads"] = int(result.stdout.strip())
            except Exception:  # nosec B110 - Hardware detection is best-effort
                pass

    # Detect GPU
    info["gpu"] = detect_gpu()

    return info


def detect_gpu():
    """Detect GPU availability and type."""
    # Check for NVIDIA GPU
    try:
        result = subprocess.run(  # nosec B603 B607
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            gpu_info = result.stdout.strip().split(",")
            return {
                "type": "nvidia",
                "name": gpu_info[0].strip(),
                "vram_mb": int(gpu_info[1].strip().split()[0]) if len(gpu_info) > 1 else 0,
            }
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass

    # Check for Apple Silicon
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return {"type": "apple_silicon", "name": "Apple Silicon", "unified_memory": True}

    return {"type": "none", "name": "CPU only"}


def recommend_model(hardware):
    """Recommend Ollama model based on hardware."""
    ram = hardware.get("ram_gb", 8)
    gpu = hardware["gpu"]["type"]

    # Model categories by resource requirements
    models = {
        "minimal": {
            "name": "phi3:mini",
            "size_gb": 2.3,
            "ram_required": 4,
            "docker_memory_gb": 5,  # Model + 2GB overhead
            "description": "Smallest, fastest - good for basic routing",
            "inference_time": "~1s (CPU)",
            "quality": "Basic",
        },
        "balanced": {
            "name": "llama3.1:8b",
            "size_gb": 4.7,
            "ram_required": 8,
            "docker_memory_gb": 8,  # Model + 3GB overhead
            "description": "Best balance of quality and speed",
            "inference_time": "~2-3s (CPU), ~500ms (GPU)",
            "quality": "Excellent",
        },
        "quality": {
            "name": "qwen2.5:7b",
            "size_gb": 4.7,
            "ram_required": 8,
            "docker_memory_gb": 10,  # Model + 5GB overhead (needs more for quality)
            "description": "Best reasoning quality for routing",
            "inference_time": "~2-3s (CPU), ~500ms (GPU)",
            "quality": "Best",
        },
        "performance": {
            "name": "mistral:7b",
            "size_gb": 4.1,
            "ram_required": 8,
            "docker_memory_gb": 7,  # Model + 3GB overhead
            "description": "Fastest 7B model, good quality",
            "inference_time": "~1-2s (CPU), ~300ms (GPU)",
            "quality": "Very Good",
        },
    }

    # Recommendation logic
    if ram < 6:
        return models[
            "minimal"
        ], "Your system has limited RAM. Using minimal model for best performance."
    elif ram < 12 and gpu == "none":
        return models["balanced"], "Balanced model recommended for your CPU-only system."
    elif gpu in ["nvidia", "apple_silicon"]:
        return models[
            "quality"
        ], "GPU detected! You can use higher quality models with fast inference."
    elif ram >= 16:
        return models["quality"], "High RAM available - quality model recommended."
    else:
        return models["balanced"], "Standard recommendation for your hardware."


def print_assessment(hardware, recommended_model, reason):
    """Print formatted assessment report."""
    print("\n" + "=" * 60)
    print("SecureClaw System Assessment")
    print("=" * 60)

    print("\n🖥️  HARDWARE DETECTED:")
    print(f"  Platform: {hardware['platform']} ({hardware['machine']})")
    cpu_cores = hardware.get("cpu_count", "Unknown")
    cpu_threads = hardware.get("cpu_threads", "Unknown")
    print(f"  CPU: {cpu_cores} cores ({cpu_threads} threads)")
    print(f"  RAM: {hardware.get('ram_gb', 'Unknown')} GB total")
    if "available_ram_gb" in hardware:
        print(f"  RAM Available: {hardware['available_ram_gb']} GB")
    if "disk_free_gb" in hardware:
        print(f"  Disk Space: {hardware['disk_free_gb']} GB free")
    print(f"  GPU: {hardware['gpu']['name']}")

    print("\n🤖 RECOMMENDED MODEL:")
    print(f"  Model: {recommended_model['name']}")
    print(f"  Download Size: {recommended_model['size_gb']} GB")
    print(f"  RAM Required: {recommended_model['ram_required']} GB minimum")
    print(f"  Docker Memory: {recommended_model['docker_memory_gb']} GB (automatically configured)")
    print(f"  Expected Speed: {recommended_model['inference_time']}")
    print(f"  Quality: {recommended_model['quality']}")
    print(f"  Description: {recommended_model['description']}")

    print("\n💡 RECOMMENDATION REASON:")
    print(f"  {reason}")

    print("\n📝 TO USE THIS MODEL:")
    print("  The model will be automatically configured in your .env file:")
    print(f"  OLLAMA_ROUTER_MODEL={recommended_model['name']}")
    print(f"  OLLAMA_DOCKER_MEMORY={recommended_model['docker_memory_gb']}")

    # Warnings
    warnings = []
    if hardware.get("disk_free_gb", 100) < recommended_model["size_gb"] * 2:
        warnings.append(
            f"⚠️  Low disk space. Ensure at least {recommended_model['size_gb'] * 2:.1f} GB free."
        )

    if hardware.get("available_ram_gb", 8) < recommended_model["ram_required"]:
        warnings.append("⚠️  Low available RAM. Close other applications before running.")

    if hardware.get("ram_gb", 8) < recommended_model["ram_required"]:
        warnings.append("⚠️  System RAM below recommended. Model may run slowly.")

    if warnings:
        print("\n⚠️  WARNINGS:")
        for warning in warnings:
            print(f"  {warning}")

    print("\n" + "=" * 60 + "\n")


def update_env_file(model_info):
    """Update .env file with recommended model and Docker memory."""
    env_path = Path(".env")

    if not env_path.exists():
        print("❌ .env file not found. Please create it first.", file=sys.stderr)
        return False

    # Read current .env
    lines = env_path.read_text().splitlines()

    # Update or add OLLAMA_ROUTER_MODEL
    model_name = model_info["name"]
    docker_memory = model_info["docker_memory_gb"]

    model_updated = False
    memory_updated = False

    # Update OLLAMA_ROUTER_MODEL
    for i, line in enumerate(lines):
        if line.startswith("OLLAMA_ROUTER_MODEL="):
            lines[i] = f"OLLAMA_ROUTER_MODEL={model_name}"
            model_updated = True
            print(f"✓ Updated OLLAMA_ROUTER_MODEL to {model_name} in .env")
            break

    if not model_updated:
        # Add after OLLAMA_HOST section
        for i, line in enumerate(lines):
            if line.startswith("OLLAMA_HOST="):
                insert_pos = i + 1
                while insert_pos < len(lines) and lines[insert_pos].startswith("OLLAMA_"):
                    insert_pos += 1
                lines.insert(insert_pos, f"OLLAMA_ROUTER_MODEL={model_name}")
                model_updated = True
                print(f"✓ Added OLLAMA_ROUTER_MODEL={model_name} to .env")
                break

        if not model_updated:
            lines.append(f"OLLAMA_ROUTER_MODEL={model_name}")
            print(f"✓ Added OLLAMA_ROUTER_MODEL={model_name} to .env")

    # Update OLLAMA_DOCKER_MEMORY
    for i, line in enumerate(lines):
        if line.startswith("OLLAMA_DOCKER_MEMORY="):
            lines[i] = f"OLLAMA_DOCKER_MEMORY={docker_memory}"
            memory_updated = True
            print(f"✓ Updated OLLAMA_DOCKER_MEMORY to {docker_memory}GB in .env")
            break

    if not memory_updated:
        # Add after OLLAMA_ROUTER_MODEL
        for i, line in enumerate(lines):
            if line.startswith("OLLAMA_ROUTER_MODEL="):
                lines.insert(i + 1, f"OLLAMA_DOCKER_MEMORY={docker_memory}")
                memory_updated = True
                print(f"✓ Added OLLAMA_DOCKER_MEMORY={docker_memory}GB to .env")
                break

        if not memory_updated:
            lines.append(f"OLLAMA_DOCKER_MEMORY={docker_memory}")
            print(f"✓ Added OLLAMA_DOCKER_MEMORY={docker_memory}GB to .env")

    # Write back to .env
    env_path.write_text("\n".join(lines) + "\n")
    return True


def main():
    """Run system assessment."""
    import argparse

    parser = argparse.ArgumentParser(description="Assess system and recommend Ollama model")
    parser.add_argument(
        "--update-env",
        action="store_true",
        help="Automatically update .env file with recommendation",
    )
    parser.add_argument(
        "--output-model", action="store_true", help="Output only the model name (for scripting)"
    )

    args = parser.parse_args()

    # Detect hardware
    hardware = detect_hardware()
    model, reason = recommend_model(hardware)

    if args.output_model:
        # Just output the model name for scripting
        print(model["name"])
        return 0

    # Print assessment
    print_assessment(hardware, model, reason)

    # Update .env if requested
    if args.update_env:
        if update_env_file(model):
            print("✅ Configuration updated successfully!")
        else:
            print("❌ Failed to update configuration.", file=sys.stderr)
            return 1
    else:
        print("💡 TIP: Run with --update-env to automatically configure this model")

    return 0


if __name__ == "__main__":
    sys.exit(main())
