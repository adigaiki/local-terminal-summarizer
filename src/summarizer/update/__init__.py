"""Update layer: opt-in check-only behavior with verification and refusal."""

from summarizer.update.install import detect_package_managers, refuse_managed_install
from summarizer.update.manager import UpdateCheck, UpdateManager, running_exe_path
from summarizer.update.release import ReleaseManifest, parse_manifest, version_newer
from summarizer.update.verify import verify_sha256, verify_signature

__all__ = [
    "UpdateManager",
    "UpdateCheck",
    "ReleaseManifest",
    "parse_manifest",
    "version_newer",
    "verify_sha256",
    "verify_signature",
    "detect_package_managers",
    "refuse_managed_install",
    "running_exe_path",
]