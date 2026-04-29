---

# GitDocs Manager (GDM)

A Python CLI to manage git submodules with **sparse checkout** and **shallow depth**. Run it inside any existing repository — it creates a `.gdm/` directory to hold all state, config, and checked-out repos.

## Features

- **Self-contained `.gdm/` directory** — all config, repos, and indexes live under `.gdm/` so your repo root stays clean
- **Sparse checkout** — only pull the files you need (docs, examples, etc.)
- **Shallow clones** — configurable depth (default: 1) to minimize download size
- **Submodule detection** — discovers existing submodules and registers them automatically
- **Whitelist & blacklist** — granular control over which repos are processed
- **Directory index** — generates `index.json` for LLM/MCP consumption
- **CLI interface** — `add`, `remove`, `sync`, `status`, `index` commands

## Project Structure

```text
your-repo/
├── gdm.py
└── .gdm/
    ├── config.json              # Shallow depth, default sparse patterns
    ├── repos.txt                # Repository URLs (one per line)
    ├── sparse-patterns.txt      # Git sparse-checkout patterns
    ├── blacklist.txt            # (Optional) Skip repos matching these
    ├── whitelist.txt            # (Optional) Only process repos matching these
    ├── index.json               # Generated directory graph
    └── repos/                   # Checked-out submodules live here
        ├── fastapi/
        └── flask/
```

## Quick Install

```bash
wget https://raw.githubusercontent.com/unclemusclez/gitdocs-manager/main/gdm.py -O gdm.py
# or
curl -L https://raw.githubusercontent.com/unclemusclez/gitdocs-manager/main/gdm.py -o gdm.py
```

## Usage

### Initialize

```bash
python gdm.py init
```

Creates the `.gdm/` directory with default config files.

### Add a repo

```bash
python gdm.py add https://github.com/fastapi/fastapi.git
python gdm.py add https://github.com/pallets/flask.git --depth 5 --sparse /docs /README.md
```

### Sync all configured repos

```bash
python gdm.py sync
python gdm.py sync --depth 3
```

### Check status

```bash
python gdm.py status
```

Shows detected submodules, their tracking status (tracked/untracked/modified/outdated), and repos not yet cloned.

### Remove a repo

```bash
python gdm.py remove fastapi
```

### Regenerate index

```bash
python gdm.py index
```

### Run from a different directory

```bash
python gdm.py -C /path/to/repo status
```

## Configuration

### `.gdm/config.json`

```json
{
    "shallow_depth": 1,
    "sparse_patterns": [
        "/*",
        "!/*",
        "/docs",
        "/Docs",
        "/examples",
        "/templates",
        "/*.md",
        "/*.txt"
    ]
}
```

### `.gdm/repos.txt`

One URL per line. Lines starting with `#` are ignored.

```text
https://github.com/fastapi/fastapi.git
https://github.com/pallets/flask.git
```

### `.gdm/sparse-patterns.txt`

Standard git sparse-checkout patterns. Defaults are synced from `config.json` on first init.

### `.gdm/blacklist.txt` / `.gdm/whitelist.txt`

- **Blacklist**: Any repo whose URL or name contains a blacklisted word is skipped
- **Whitelist**: If non-empty, only repos matching a whitelisted word are processed

## How It Works

1. **Add/Sync**: Clones repos as shallow (`--depth N`) into `.gdm/repos/`, applies sparse checkout, and registers the URL in `repos.txt`
2. **Detection**: Scans `.gdm/repos/` for any git directories, resolves their remote URLs, and auto-registers unlisted allowed repos
3. **Tracking**: Uses `git submodule status` to determine if repos are git-tracked submodules, and whether they're modified or outdated
4. **Indexing**: Walks all checked-out repos and generates a file manifest in `.gdm/index.json`

## Requirements

- **Python 3.8+**
- **Git 2.25+** (sparse-checkout support)

## License

MIT
