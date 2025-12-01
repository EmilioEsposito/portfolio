import os


# Local PostgreSQL connection string for remote Claude Code environment
# Matches docker-compose.yml: postgresql://portfolio:portfolio@postgres:5432/portfolio
# but uses localhost since we're running PostgreSQL directly (not in Docker)
LOCAL_DATABASE_URL = "postgresql://portfolio:portfolio@localhost:5432/portfolio"


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
        # Ignore comments and empty lines
        if not stripped or stripped.startswith("#"):
            continue
        # Remove quotes around values if present
        if "=" in stripped:
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip()
            # Remove leading/trailing quotes
            if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
                value = value[1:-1]

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
    new_env_var_format = "\n".join(strict_lines)

    file_path = os.path.join(repo_root, '.claude/.env.claude.remote')
    with open(file_path, 'w') as file:
        file.write(new_env_var_format)
    print(f"Done writing to {file_path}")

if __name__ == "__main__":
    main()
