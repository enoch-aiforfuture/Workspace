"""GHCR image names must be lowercase.

``github.repository`` keeps the GitHub repo's original casing. Publishing
``ghcr.io/enoch-aiforfuture/Workspace`` fails with "repository name must be
lowercase". The docker publish workflow lowercases that ref before every
tag and push.
"""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github/workflows/docker-publish.yml"


def test_docker_publish_lowercases_image_name_before_push():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "IMAGE_NAME: ${{ github.repository }}" not in workflow
    assert workflow.count("${GITHUB_REPOSITORY,,}") == 2
    assert workflow.count("id: image") == 2
    assert "name=${{ env.REGISTRY }}/${{ steps.image.outputs.name }}" in workflow
    assert "images: ${{ env.REGISTRY }}/${{ steps.image.outputs.name }}" in workflow
    assert workflow.count("IMAGE_NAME: ${{ steps.image.outputs.name }}") == 2


COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.gpu-nvidia.yml",
    "docker-compose.gpu-amd.yml",
)
GHCR_LATEST = "ghcr.io/enoch-aiforfuture/workspace:latest"


def test_compose_defaults_to_lowercase_ghcr_image():
    """A bare workspace:latest tag is Docker Hub, not the image CI publishes."""
    for name in COMPOSE_FILES:
        text = (REPO / name).read_text(encoding="utf-8")
        assert f"${{WORKSPACE_IMAGE:-{GHCR_LATEST}}}" in text, name
        assert "WORKSPACE_IMAGE:-workspace:latest" not in text, name
        assert "enoch-aiforfuture/Workspace" not in text, name


def test_docs_pin_the_lowercase_ghcr_image():
    for name in ("README.md", "website/setup.md"):
        text = (REPO / name).read_text(encoding="utf-8")
        assert "ghcr.io/enoch-aiforfuture/workspace:" in text, name
        assert "WORKSPACE_IMAGE=workspace:" not in text, name


def test_bash_lowercase_matches_ghcr_repository_rule():
    """The workflow's ${VAR,,} expansion is what buildx will actually receive."""
    result = subprocess.run(
        ["bash", "-c", 'name="${GITHUB_REPOSITORY,,}"; printf "%s" "$name"'],
        env={**os.environ, "GITHUB_REPOSITORY": "enoch-aiforfuture/Workspace"},
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert result.stdout == "enoch-aiforfuture/workspace"
    assert result.stdout == result.stdout.lower()
