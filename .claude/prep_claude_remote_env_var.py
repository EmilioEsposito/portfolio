import os


# Local PostgreSQL connection string for remote Claude Code environment
# Matches docker-compose.yml: postgresql://portfolio:portfolio@postgres:5432/portfolio
# but uses localhost since we're running PostgreSQL directly (not in Docker)
LOCAL_DATABASE_URL = "postgresql://portfolio:portfolio@localhost:5432/portfolio"

# Keys to pull from ~/.zprofile for Claude Code dev tooling (not used by the app itself)
ZPROFILE_KEYS = [
    "LOGFIRE_MCP_API_KEY",
    "RAILWAY_MCP_TOKEN",
    "GH_TOKEN",
    "NEON_MCP_API_KEY",
]

# Keys from .env that must NOT be injected into the Claude Code remote env.
# Empty by default — our app's Anthropic key lives under SERNIA_ANTHROPIC_API_KEY
# (see api/index.py for the bridge to ANTHROPIC_API_KEY) so it no longer
# collides with the ANTHROPIC_API_KEY Claude Cloud injects for itself.
SKIP_KEYS: set[str] = set()


def parse_zprofile_secrets(keys):
    """Extract `export KEY=VALUE` lines from ~/.zprofile matching the given keys."""
    zprofile_path = os.path.expanduser("~/.zprofile")
    if not os.path.exists(zprofile_path):
        return {}

    with open(zprofile_path, 'r') as file:
        lines = file.read().splitlines()

    wanted = set(keys)
    found = {}
    for line in lines:
        # Handle chained statements like `export FOO=bar; export BAZ=qux`
        for segment in line.split(";"):
            stripped = segment.strip()
            if not stripped.startswith("export "):
                continue
            assignment = stripped[len("export "):]
            if "=" not in assignment:
                continue
            key, value = assignment.split("=", 1)
            key = key.strip()
            if key not in wanted:
                continue
            value = value.strip()
            if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
                value = value[1:-1]
            found[key] = value
    return found


def main():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(current_dir)

    with open(os.path.join(repo_root, '.env'), 'r') as file:
        env_var_file_str = file.read()

    # Convert to strict .env format: no comments, no empty lines, no trailing whitespace, no quotes around values
    lines = env_var_file_str.splitlines()
    strict_lines = []
    for line in lines:
        stripped = line.strip()
        # Ignore comments and empty lines (`;` is also used as a comment marker)
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        # Remove quotes around values if present
        if "=" in stripped:
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip()
            # Remove leading/trailing quotes
            if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
                value = value[1:-1]

            if key in SKIP_KEYS:
                print(f"Warning: skipping {key} — collides with Claude Cloud's own env var. Rename in app code/Railway/.env to remove this skip.")
                continue

            # Override database URLs when running in remote environment
            if key in ("DATABASE_URL", "DATABASE_URL_UNPOOLED"):
                value = LOCAL_DATABASE_URL
                print(f"Overriding {key} for remote environment")
            elif key == "DATABASE_REQUIRE_SSL":
                value = "false"
                print(f"Overriding {key}=false for remote environment")

            new_line = f"{key}={value.strip()}"
            strict_lines.append(new_line)
        else:
            # unhandled case, preserve as-is (rare, shouldn't happen in .env)
            strict_lines.append(stripped)

    zprofile_secrets = parse_zprofile_secrets(ZPROFILE_KEYS)
    existing_keys = {line.split("=", 1)[0] for line in strict_lines if "=" in line}
    for key in ZPROFILE_KEYS:
        if key in existing_keys:
            print(f"Skipping {key} from ~/.zprofile (already set in .env)")
            continue
        if key not in zprofile_secrets:
            print(f"Warning: {key} not found in ~/.zprofile")
            continue
        strict_lines.append(f"{key}={zprofile_secrets[key]}")
        existing_keys.add(key)
        print(f"Adding {key} from ~/.zprofile")

    new_env_var_format = "\n".join(strict_lines)

    file_path = os.path.join(repo_root, '.claude/.env.claude.remote')
    with open(file_path, 'w') as file:
        file.write(new_env_var_format)
    print(f"Done writing to {file_path}")

if __name__ == "__main__":
    main()
