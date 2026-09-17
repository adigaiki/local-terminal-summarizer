"""Package-manager detection and refusal.

summarizer must never overwrite an installation managed by pipx, brew,
pacman, apt/dpkg, dnf/yum, cargo, or other package managers. This module
implements conservative path-based detection: when the running executable
resolves into a directory tree that belongs to a known package manager, an
automatic in-place replacement is refused with an actionable hint.
"""

from __future__ import annotations

from pathlib import Path

from summarizer.errors import UpdateError

__all__ = ["detect_package_managers", "refuse_managed_install"]


def detect_package_managers(exe_path: Path) -> list[str]:
    """Return names of package managers that plausibly control `exe_path`."""
    try:
        exe = exe_path.resolve()
    except OSError:
        exe = exe_path
    text = str(exe)

    hints: list[tuple[str, str]] = []
    if any(part in text for part in ("/pipx/", ".local/pipx", "/interpreter-wrappers/", "/venvs/")):
        hints.append(("pipx", "/pipx/", ".local/pipx", "/interpreter-wrappers/", "/venvs/"))
    if any(part in text for part in ("/homebrew/", "/opt/homebrew/", "/linuxbrew/", "/brew/")):
        hints.append(("brew", "/homebrew/", "/opt/homebrew/", "/linuxbrew/", "/brew/"))
    if any(part in text for part in ("/cargo/", "/.cargo/")):
        hints.append(("cargo", "/cargo/", "/.cargo/"))
    if "/flatpak/" in text or "/var/lib/flatpak/" in text:
        hints.append(("flatpak",))
    if "/snap/" in text or "/var/lib/snapd/" in text:
        hints.append(("snap",))
    # System Python installs (deb/rpm vendored or distro pip) live under
    # /usr/lib/python*/dist-packages or /usr/lib/*/site-packages.
    if "/usr/lib/python" in text or "/usr/lib64/python" in text:
        hints.append(("system package (apt/dnf)",))

    found: list[str] = []
    seen: set[str] = set()
    for entry in hints:
        name = entry[0]
        matched = any(part in text for part in entry[1:]) or len(entry) == 1
        if matched and name not in seen:
            seen.add(name)
            found.append(name)
    return found


def refuse_managed_install(exe_path: Path) -> None:
    """Raise UpdateError when the executable appears package-managed."""
    managers = detect_package_managers(exe_path)
    if managers:
        raise UpdateError(
            f"refusing to replace an installation managed by: {', '.join(managers)}",
            hint="install or update through the package manager that owns it "
                 "instead of letting summarizer replace the binary",
        )