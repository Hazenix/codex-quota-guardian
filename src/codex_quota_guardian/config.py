from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Callable


MANAGED_MARKER = "# managed-by: codex-quota-guardian"
DEEPSEEK_SECTION = "model_providers.deepseek"
TOP_LEVEL_KEYS = {
    "model",
    "model_provider",
    "model_reasoning_effort",
    "web_search",
    "show_raw_agent_reasoning",
    "model_catalog_json",
}
DESKTOP_REASONING_KEY = "enabled-reasoning-efforts"
SECTION_PATTERN = re.compile(r"^\s*\[([^]]+)]\s*(?:#.*)?$")
KEY_PATTERN = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")


class ConfigConflictError(RuntimeError):
    def __init__(self, message: str, proposed_restore_path: Path = None):
        super().__init__(message)
        self.proposed_restore_path = proposed_restore_path


class ConfigValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SwitchResult:
    backup_path: str
    managed_hash: str
    original_hash: str
    original_existed: bool = True


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def validate_codex_config(
    candidate_path: Path,
    codex_path: str,
    runner=subprocess.run,
) -> None:
    """Ask the installed Codex binary to strictly load an isolated config."""
    with tempfile.TemporaryDirectory(prefix="codex-quota-guardian-validate-") as directory:
        isolated_home = Path(directory)
        shutil.copyfile(candidate_path, isolated_home / "config.toml")
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(isolated_home)
        result = runner(
            [codex_path, "app-server", "--strict-config", "--stdio"],
            input=(
                '{"jsonrpc":"2.0","id":1,"method":"initialize",'
                '"params":{"clientInfo":{"name":"codex-quota-guardian-validator",'
                '"version":"0.1.0"}}}\n'
            ),
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
            env=environment,
        )
        if result.returncode != 0:
            raise ConfigValidationError(
                "Codex rejected the generated config; the active config was not changed"
            )
        try:
            messages = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        except json.JSONDecodeError as error:
            raise ConfigValidationError("Codex config validation returned invalid JSON") from error
        initialized = next((message for message in messages if message.get("id") == 1), None)
        if not initialized or "error" in initialized or "result" not in initialized:
            raise ConfigValidationError("Codex did not accept the generated config")


def build_deepseek_config(
    original: str,
    model: str,
    model_catalog_path: Path,
    auth_helper_path: Path,
) -> str:
    if model not in {"deepseek-flash", "deepseek-v4-pro"}:
        raise ValueError("Unsupported DeepSeek model")
    lines = original.splitlines(keepends=True)
    sections = _section_ranges(lines)
    deepseek_ranges = [
        item for item in sections if item[0] == DEEPSEEK_SECTION or item[0].startswith(f"{DEEPSEEK_SECTION}.")
    ]
    managed_parent = any(
        name == DEEPSEEK_SECTION
        and start > 0
        and lines[start - 1].strip() == MANAGED_MARKER
        for name, start, _ in deepseek_ranges
    )
    if deepseek_ranges and not managed_parent:
        raise ConfigConflictError(
            "An unmanaged [model_providers.deepseek] configuration already exists"
        )

    if deepseek_ranges:
        remove_indexes = set()
        for _, start, end in deepseek_ranges:
            remove_indexes.update(range(start, end))
            if start > 0 and lines[start - 1].strip() == MANAGED_MARKER:
                remove_indexes.add(start - 1)
        lines = [line for index, line in enumerate(lines) if index not in remove_indexes]

    structural_flags = _structural_flags(lines)
    first_section = next((start for _, start, _ in _section_ranges(lines)), len(lines))
    root = [
        line
        for index, line in enumerate(lines[:first_section])
        if not (
            structural_flags[index]
            and _line_assigns_any_key(line, TOP_LEVEL_KEYS)
        )
    ]
    remainder = lines[first_section:]
    root = _trim_trailing_blank_lines(root)
    root.extend(_deepseek_root_lines(model, model_catalog_path))
    lines = root + remainder

    lines = _upsert_desktop_reasoning(lines)
    lines = _trim_trailing_blank_lines(lines)
    lines.extend(_deepseek_provider_lines(auth_helper_path))
    return "".join(lines)


def _deepseek_root_lines(model: str, catalog: Path):
    web_search = "live" if model == "deepseek-v4-pro" else "disabled"
    return [
        f'model = {json.dumps(model)}\n',
        'model_provider = "deepseek"\n',
        'model_reasoning_effort = "high"\n',
        f'web_search = {json.dumps(web_search)}\n',
        'show_raw_agent_reasoning = true\n',
        f'model_catalog_json = {json.dumps(str(catalog))}\n',
        "\n",
    ]


def _deepseek_provider_lines(auth_helper_path: Path):
    return [
        MANAGED_MARKER + "\n",
        "[model_providers.deepseek]\n",
        'name = "deepseek"\n',
        'base_url = "https://api.deepseek.com/"\n',
        'wire_api = "responses"\n',
        "\n",
        "[model_providers.deepseek.auth]\n",
        f'command = {json.dumps(str(auth_helper_path))}\n',
        "refresh_interval_ms = 0\n",
    ]


def _upsert_desktop_reasoning(lines):
    ranges = _section_ranges(lines)
    desktop = next((item for item in ranges if item[0] == "desktop"), None)
    setting = (
        'enabled-reasoning-efforts = ["low", "medium", "high", "xhigh", "ultra", "max"]\n'
    )
    if desktop is None:
        lines = _trim_trailing_blank_lines(lines)
        lines.extend(["[desktop]\n", setting, "\n"])
        return lines

    _, start, end = desktop
    structural_flags = _structural_flags(lines)
    body = []
    for index in range(start + 1, end):
        line = lines[index]
        if structural_flags[index] and _line_assigns_any_key(
            line, {DESKTOP_REASONING_KEY}
        ):
            continue
        body.append(line)
    return lines[: start + 1] + [setting] + body + lines[end:]


def _section_ranges(lines):
    starts = []
    structural_flags = _structural_flags(lines)
    for index, line in enumerate(lines):
        if not structural_flags[index]:
            continue
        match = SECTION_PATTERN.match(line)
        if match:
            starts.append((match.group(1).strip(), index))
    ranges = []
    for position, (name, start) in enumerate(starts):
        end = starts[position + 1][1] if position + 1 < len(starts) else len(lines)
        ranges.append((name, start, end))
    return ranges


def _structural_flags(lines):
    """Identify lines that begin outside TOML strings and multiline values."""
    flags = []
    multiline = None
    bracket_depth = 0
    for line in lines:
        flags.append(multiline is None and bracket_depth == 0)
        index = 0
        quote = None
        while index < len(line):
            if multiline is not None:
                delimiter = '"""' if multiline == "basic" else "'''"
                if line.startswith(delimiter, index):
                    multiline = None
                    index += 3
                    continue
                if multiline == "basic" and line[index] == "\\":
                    index += 2
                    continue
                index += 1
                continue

            character = line[index]
            if quote is not None:
                if quote == '"' and character == "\\":
                    index += 2
                    continue
                if character == quote:
                    quote = None
                index += 1
                continue

            if character == "#":
                break
            if line.startswith('"""', index):
                multiline = "basic"
                index += 3
                continue
            if line.startswith("'''", index):
                multiline = "literal"
                index += 3
                continue
            if character in {'"', "'"}:
                quote = character
            elif character in "[{":
                bracket_depth += 1
            elif character in "]}":
                bracket_depth = max(0, bracket_depth - 1)
            index += 1
    return flags


def _line_assigns_any_key(line, keys):
    match = KEY_PATTERN.match(line)
    return bool(match and match.group(1) in keys)


def _trim_trailing_blank_lines(lines):
    result = list(lines)
    while result and not result[-1].strip():
        result.pop()
    if result and not result[-1].endswith("\n"):
        result[-1] += "\n"
    if result:
        result.append("\n")
    return result


class ConfigManager:
    def __init__(self, config_path: Path, backup_dir: Path):
        self.config_path = Path(config_path)
        self.backup_dir = Path(backup_dir)

    def switch_to_deepseek(
        self,
        model: str,
        model_catalog_path: Path,
        auth_helper_path: Path,
        validate: Callable[[Path], None],
    ) -> SwitchResult:
        original_existed = self.config_path.exists()
        original = self.config_path.read_bytes() if original_existed else b""
        original_hash = sha256_bytes(original)
        rendered = build_deepseek_config(
            original.decode("utf-8"),
            model=model,
            model_catalog_path=model_catalog_path,
            auth_helper_path=auth_helper_path,
        ).encode("utf-8")

        self.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.backup_dir, 0o700)
        backup_path = self.backup_dir / f"config-{time.time_ns()}.toml"
        _write_private_atomic(backup_path, original)
        _replace_after_validation(self.config_path, rendered, validate)
        return SwitchResult(
            backup_path=str(backup_path),
            managed_hash=sha256_bytes(rendered),
            original_hash=original_hash,
            original_existed=original_existed,
        )

    def restore(
        self,
        backup_path: Path,
        expected_managed_hash: str,
        original_existed: bool = True,
    ) -> str:
        current = self.config_path.read_bytes() if self.config_path.exists() else b""
        backup = Path(backup_path).read_bytes()
        if sha256_bytes(current) != expected_managed_hash:
            proposed_path = self.backup_dir / f"restore-proposed-{time.time_ns()}.toml"
            _write_private_atomic(proposed_path, backup)
            raise ConfigConflictError(
                "The active config changed after the DeepSeek switch; refusing to overwrite it. "
                f"Proposed restore: {proposed_path}",
                proposed_restore_path=proposed_path,
            )
        if original_existed:
            _write_private_atomic(self.config_path, backup)
        else:
            self.config_path.unlink()
        return sha256_bytes(backup)


def _replace_after_validation(path: Path, content: bytes, validate: Callable[[Path], None]):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = _write_private_temp(path.parent, f".{path.name}.", content)
    try:
        validate(temporary_path)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _write_private_atomic(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = _write_private_temp(path.parent, f".{path.name}.", content)
    try:
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _write_private_temp(directory: Path, prefix: str, content: bytes) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(prefix=prefix, dir=str(directory))
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        return temporary_path
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise
