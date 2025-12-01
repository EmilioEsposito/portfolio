import os


def main():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(current_dir)

    with open(os.path.join(repo_root, '.env'), 'r') as file:
        env_var_file_str = file.read()

    # TODO: convert to strict .env format. no comments, no empty lines, no trailing whitespace, no quotes around values
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
            value = value.strip()
            # Remove leading/trailing quotes
            if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
                value = value[1:-1]
            # Remove trailing whitespace around key and value
            new_line = f"{key.strip()}={value.strip()}"
            strict_lines.append(new_line)
        else:
            # unhandled case, preserve as-is (rare, shouldn't happen in .env)
            strict_lines.append(stripped)
    new_env_var_format = "\n".join(strict_lines)

    file_path = os.path.join(repo_root, '.claude/.env.claude')
    with open(file_path, 'w') as file:
        file.write(new_env_var_format)
    print(f"Done writing to {file_path}")

if __name__ == "__main__":
    main()
