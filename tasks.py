"""Task automation using ewok (invoke-compatible)."""

import re
from pathlib import Path

from ewok import Context, task

# Compiled regex pattern for replacing the nav section in mkdocs.yml
NAV_SECTION_PATTERN = re.compile(r"nav:.*?(?=\n[a-z_]+:|$)", re.DOTALL)


def extract_title(md_file: Path) -> str:
    """Extract the title from a markdown file's first heading."""
    first_line = md_file.read_text(encoding="utf-8").split("\n", 1)[0].strip()
    # Remove the leading # and any extra whitespace
    return first_line.lstrip("#").strip()


def generate_nav_entries() -> list[str]:
    """Generate nav entries from numbered markdown files in docs/."""
    docs_dir = Path(__file__).parent / "docs"

    # Find all numbered chapter files (supports 1-N digits)
    chapters = [f for f in docs_dir.glob("*_*.md") if f.stem.split("_", 1)[0].isdigit()]

    return [
        f"  - {extract_title(chapter)}: {chapter.name}"
        for chapter in sorted(
            chapters,
            key=lambda f: int(f.stem.split("_", 1)[0]),
        )
    ]


@task
def update_docs_nav(ctx: Context) -> None:
    """Update mkdocs.yml nav section from actual markdown files to prevent sync issues."""
    mkdocs_file = Path(__file__).parent / "mkdocs.yml"

    content = mkdocs_file.read_text(encoding="utf-8")

    # Generate new nav entries
    nav_entries = generate_nav_entries()
    new_nav_section = "nav:\n" + "\n".join(nav_entries)

    # Replace the nav section
    updated_content = NAV_SECTION_PATTERN.sub(new_nav_section, content)

    mkdocs_file.write_text(updated_content, encoding="utf-8")

    print(f"✓ Updated mkdocs.yml with {len(nav_entries)} chapters")
    print("\nGenerated nav:")
    for entry in nav_entries:
        print(entry)
