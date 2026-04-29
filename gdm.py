import subprocess
import json
import argparse
from pathlib import Path


class GitDocsManager:
    GDM_DIR = ".gdm"
    CONFIG_FILE = "config.json"
    REPOS_FILE = "repos.txt"
    SPARSE_FILE = "sparse-patterns.txt"
    BLACKLIST_FILE = "blacklist.txt"
    WHITELIST_FILE = "whitelist.txt"
    INDEX_FILE = "index.json"

    def __init__(self, root=None):
        self.root = Path(root or Path.cwd()).resolve()
        self.gdm_dir = self.root / self.GDM_DIR
        self.config_path = self.gdm_dir / self.CONFIG_FILE

        self._init_gdm()
        self._load_config()

    def _run_git(self, args, cwd=None, check=False):
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd or self.root,
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result

    def _init_gdm(self):
        if not self.gdm_dir.exists():
            self.gdm_dir.mkdir(parents=True)

        if not self.config_path.exists():
            default_config = {
                "shallow_depth": 1,
                "sparse_patterns": [
                    "/*",
                    "!/*",
                    "docs/",
                    "*Docs/",
                    "*examples/",
                    "*templates/",
                    "*/docs/",
                    "*/Docs/",
                    "*/examples/",
                    "*/templates/",
                    "/*.md*",
                    "/*.txt",
                    "/*.rst",
                    "/*.lst"
                ],
            }
            self._write_json(self.config_path, default_config)

        for fname in [self.REPOS_FILE, self.BLACKLIST_FILE, self.WHITELIST_FILE]:
            p = self.gdm_dir / fname
            if not p.exists():
                p.touch()

        sparse_file = self.gdm_dir / self.SPARSE_FILE
        if not sparse_file.exists():
            config = self._read_json(self.config_path)
            patterns = config.get("sparse_patterns", [])
            sparse_file.write_text("\n".join(patterns) + "\n")

    def _load_config(self):
        self.config = self._read_json(self.config_path)
        self.shallow_depth = self.config.get("shallow_depth", 1)
        self.target_repos = self._load_file(self.gdm_dir / self.REPOS_FILE)
        self.blacklist = self._load_file(self.gdm_dir / self.BLACKLIST_FILE)
        self.whitelist = self._load_file(self.gdm_dir / self.WHITELIST_FILE)
        self.sparse_patterns = self._load_file(self.gdm_dir / self.SPARSE_FILE)

    def _read_json(self, path):
        with open(path, "r") as f:
            return json.load(f)

    def _write_json(self, path, data):
        with open(path, "w") as f:
            json.dump(data, f, indent=4)

    def _load_file(self, path):
        if not path.exists():
            return []
        with open(path, "r") as f:
            return [line.strip() for line in f if line.strip() and not line.startswith("#")]

    def _append_file(self, path, line):
        with open(path, "a") as f:
            f.write(f"{line}\n")

    def _write_file(self, path, content):
        with open(path, "w") as f:
            f.write(content)

    def is_allowed(self, url_or_name):
        if any(item in url_or_name for item in self.blacklist):
            return False
        if self.whitelist:
            return any(item in url_or_name for item in self.whitelist)
        return True

    def _repo_name_from_url(self, url):
        return url.rstrip("/").split("/")[-1].replace(".git", "")

    def _detect_submodules(self):
        submodules = {}
        res = self._run_git(["submodule", "status", "--recursive"])
        if res.returncode != 0:
            return submodules
        for line in res.stdout.strip().splitlines():
            if not line.strip():
                continue
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            prefix = parts[0]
            sha = prefix.lstrip("+-")
            rel_path = parts[1]
            abs_path = self.root / rel_path

            remote_url = None
            if abs_path.exists():
                r = self._run_git(["remote", "get-url", "origin"], cwd=abs_path)
                if r.returncode == 0:
                    remote_url = r.stdout.strip()

            submodules[rel_path] = {
                "sha": sha,
                "modified": prefix.startswith("+"),
                "outdated": prefix.startswith("-"),
                "remote_url": remote_url,
                "abs_path": abs_path,
            }
        return submodules

    def add(self, url, sparse=None, depth=None, merge=True):
        if not self.is_allowed(url):
            print(f"Blocked: {url} is not allowed (blacklisted or not whitelisted)")
            return

        name = self._repo_name_from_url(url)
        submodules = self._detect_submodules()

        for rel_path, info in submodules.items():
            if info["remote_url"] == url or Path(rel_path).name == name:
                print(f"Already exists at {rel_path}. Use 'sync' to update.")
                return

        effective_depth = depth or self.shallow_depth

        print(f"Adding {name} (depth={effective_depth})")
        res = self._run_git(
            ["submodule", "add", "--depth", str(effective_depth), url, name],
        )
        if res.returncode != 0:
            print(f"submodule add failed: {res.stderr.strip()}")
            return

        repo_path = self.root / name
        self._apply_sparse_checkout(repo_path, sparse, merge=merge)
        self._run_git(["checkout", "HEAD"], cwd=repo_path)

        if url not in self.target_repos:
            self._append_file(self.gdm_dir / self.REPOS_FILE, url)
            self.target_repos.append(url)

        print(f"Added: {name}")

    def remove(self, name):
        submodules = self._detect_submodules()
        match_path = None
        for rel_path, info in submodules.items():
            if Path(rel_path).name == name:
                match_path = rel_path
                break

        if not match_path:
            print(f"Not found: {name}")
            return

        abs_path = self.root / match_path

        self._run_git(["submodule", "deinit", "-f", match_path])
        self._run_git(["rm", "-rf", match_path])

        if abs_path.exists():
            import shutil
            shutil.rmtree(abs_path, ignore_errors=True)

        self.target_repos = [u for u in self.target_repos if self._repo_name_from_url(u) != name]
        self._write_file(self.gdm_dir / self.REPOS_FILE, "\n".join(self.target_repos) + "\n" if self.target_repos else "")

        print(f"Removed: {name}")

    def _resolve_git_dir(self, repo_path):
        git_ref = repo_path / ".git"
        if git_ref.is_file():
            text = git_ref.read_text().strip()
            if text.startswith("gitdir:"):
                git_dir = Path(text.split(":", 1)[1].strip())
                if git_dir.is_absolute():
                    return git_dir
                return (repo_path / git_dir).resolve()
        if git_ref.is_dir():
            return git_ref
        return None

    def _resolve_sparse_checkout_file(self, repo_path):
        git_dir = self._resolve_git_dir(repo_path)
        if git_dir:
            return git_dir / "info" / "sparse-checkout"
        return repo_path / ".git" / "info" / "sparse-checkout"

    def _read_existing_sparse_patterns(self, repo_path):
        sparse_file = self._resolve_sparse_checkout_file(repo_path)
        if not sparse_file.exists():
            return []
        lines = sparse_file.read_text().splitlines()
        return [l.strip() for l in lines if l.strip() and not l.startswith("#")]

    def _merge_sparse_patterns(self, existing, incoming):
        merged = list(existing)
        for p in incoming:
            if p not in merged:
                merged.append(p)
        return merged

    def _show_sparse_diff(self, repo_name, before, after):
        removed = [p for p in before if p not in after]
        added = [p for p in after if p not in before]
        if not removed and not added:
            return False
        print(f"  Sparse-checkout changes for {repo_name}:")
        for p in removed:
            print(f"    - {p}")
        for p in added:
            print(f"    + {p}")
        return True

    def _apply_sparse_checkout(self, repo_path, patterns=None, merge=True):
        incoming = patterns or self.sparse_patterns
        if not incoming:
            return

        existing = self._read_existing_sparse_patterns(repo_path) if merge else []
        effective_patterns = self._merge_sparse_patterns(existing, incoming) if merge else incoming

        if existing and merge:
            if not self._show_sparse_diff(repo_path.name, existing, effective_patterns):
                print(f"  Sparse-checkout already up to date")
                return

        self._run_git(["sparse-checkout", "init", "--cone"], cwd=repo_path)
        self._run_git(["sparse-checkout", "set"] + effective_patterns, cwd=repo_path)

        sparse_file = self._resolve_sparse_checkout_file(repo_path)
        git_dir = self._resolve_git_dir(repo_path)
        if git_dir and not git_dir.exists():
            git_dir.mkdir(parents=True, exist_ok=True)
        info_dir = sparse_file.parent
        if info_dir.exists() or (git_dir and info_dir.parent == git_dir):
            info_dir.mkdir(parents=True, exist_ok=True)
        sparse_file.write_text("\n".join(effective_patterns) + "\n")

    def desparse(self, names):
        submodules = self._detect_submodules()
        found = {Path(rp).name: info for rp, info in submodules.items()}
        done = 0
        for name in names:
            info = found.get(name)
            if not info:
                print(f"Not found: {name}")
                continue
            repo_path = info["abs_path"]
            if not repo_path.exists():
                print(f"Skipping {name} (not checked out)")
                continue
            before = self._read_existing_sparse_patterns(repo_path)
            self._run_git(["sparse-checkout", "disable"], cwd=repo_path)
            after = self._read_existing_sparse_patterns(repo_path)
            if before:
                self._show_sparse_diff(name, before, after)
            print(f"  Disabled sparse-checkout for {name}")
            done += 1
        if done:
            print(f"Desparsed {done} repo(s)")

    def _register_untracked(self, submodules):
        registered = 0
        for rel_path, info in submodules.items():
            name = Path(rel_path).name
            remote_url = info.get("remote_url")
            if not self.is_allowed(name) and not self.is_allowed(remote_url or ""):
                continue
            if remote_url and remote_url not in self.target_repos:
                print(f"Registering discovered submodule: {rel_path} -> {remote_url}")
                self._append_file(self.gdm_dir / self.REPOS_FILE, remote_url)
                self.target_repos.append(remote_url)
                registered += 1
        return registered

    def sync(self, merge_sparse=True):
        submodules = self._detect_submodules()
        print(f"Detected {len(submodules)} submodules")

        self._register_untracked(submodules)

        for url in self.target_repos:
            name = self._repo_name_from_url(url)
            if not self.is_allowed(url) and not self.is_allowed(name):
                print(f"Skipping {name} (not allowed)")
                continue

            match = None
            for rel_path, info in submodules.items():
                if info.get("remote_url") == url or Path(rel_path).name == name:
                    match = (rel_path, info)
                    break

            if match:
                rel_path, info = match
                repo_path = info["abs_path"]
                if not repo_path.exists():
                    print(f"Initializing: {rel_path}")
                    self._run_git(["submodule", "update", "--init", rel_path])
                else:
                    print(f"Updating: {rel_path}")
                    self._run_git(["submodule", "update", "--remote", rel_path])
                self._apply_sparse_checkout(repo_path, merge=merge_sparse)
                self._run_git(["checkout", "HEAD"], cwd=repo_path)
            else:
                print(f"Adding: {name}")
                self._run_git(
                    ["submodule", "add", "--depth", str(self.shallow_depth), url, name]
                )
                repo_path = self.root / name
                self._apply_sparse_checkout(repo_path, merge=False)
                self._run_git(["checkout", "HEAD"], cwd=repo_path)

        self.generate_index()

    def status(self):
        submodules = self._detect_submodules()

        print(f"\nGDM Status ({self.root})")
        print(f"{'='*60}")
        print(f"Configured repos:  {len(self.target_repos)}")
        print(f"Detected submodules: {len(submodules)}")
        print(f"Shallow depth:     {self.shallow_depth}")
        print(f"Sparse patterns:   {len(self.sparse_patterns)}")

        if submodules:
            print(f"\n{'Path':<35} {'Remote':<45} {'Status'}")
            print(f"{'-'*35} {'-'*45} {'-'*20}")
            for rel_path in sorted(submodules):
                info = submodules[rel_path]
                if info.get("modified"):
                    status = "modified"
                elif info.get("outdated"):
                    status = "outdated"
                else:
                    status = "ok"
                remote = info.get("remote_url") or "unknown"
                if not self.is_allowed(Path(rel_path).name):
                    status = "blocked"
                elif info.get("remote_url") and info["remote_url"] not in self.target_repos:
                    status += ", unregistered"
                print(f"{rel_path:<35} {remote:<45} {status}")

        configured_names = set()
        for url in self.target_repos:
            configured_names.add(self._repo_name_from_url(url))
        detected_names = {Path(p).name for p in submodules}

        missing = configured_names - detected_names
        if missing:
            print(f"\nNot yet added as submodules:")
            for name in sorted(missing):
                print(f"  {name}")

    def generate_index(self):
        submodules = self._detect_submodules()
        manifest = {}

        for rel_path, info in submodules.items():
            name = Path(rel_path).name
            if not self.is_allowed(name):
                continue

            repo_path = info["abs_path"]
            if not repo_path.exists():
                continue

            manifest[name] = sorted(
                str(p.relative_to(repo_path))
                for p in repo_path.rglob("*")
                if p.is_file() and ".git" not in p.parts
            )

        index_path = self.gdm_dir / self.INDEX_FILE
        self._write_json(index_path, {
            "graph": manifest,
            "root": str(self.root),
            "shallow_depth": self.shallow_depth,
        })
        print(f"\nIndex generated: {index_path} ({len(manifest)} repos)")


def main():
    parser = argparse.ArgumentParser(
        prog="gdm",
        description="GitDocs Manager - manage git submodules with sparse checkout and shallow depth",
    )
    parser.add_argument("-C", "--root", default=None, help="Run as if started in ROOT instead of cwd")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize .gdm directory in the current repo")

    sync_parser = sub.add_parser("sync", help="Clone/update all configured repos")
    sync_parser.add_argument("--depth", type=int, default=None, help="Override shallow depth")
    sync_parser.add_argument("--no-merge", action="store_true", help="Overwrite existing sparse-checkout rules instead of merging")

    add_parser = sub.add_parser("add", help="Add a new submodule")
    add_parser.add_argument("url", help="Repository URL to add")
    add_parser.add_argument("--sparse", nargs="*", default=None, help="Sparse checkout patterns (overrides config)")
    add_parser.add_argument("--depth", type=int, default=None, help="Override shallow depth for this repo")
    add_parser.add_argument("--no-merge", action="store_true", help="Overwrite existing sparse-checkout rules instead of merging")

    remove_parser = sub.add_parser("remove", help="Remove a submodule")
    remove_parser.add_argument("name", help="Repository name to remove")

    desparse_parser = sub.add_parser("desparse", help="Disable sparse-checkout for specific repos")
    desparse_parser.add_argument("names", nargs="+", help="Repository name(s) to desparse")

    sub.add_parser("status", help="Show status of all managed submodules")

    sub.add_parser("index", help="Regenerate the directory index")

    args = parser.parse_args()

    root = args.root or Path.cwd()
    gdm = GitDocsManager(root=root)

    if args.command == "init":
        print(f"GDM initialized at {gdm.gdm_dir}")
    elif args.command == "sync":
        if args.depth:
            gdm.shallow_depth = args.depth
        gdm.sync(merge_sparse=not args.no_merge)
    elif args.command == "add":
        gdm.add(args.url, sparse=args.sparse, depth=args.depth, merge=not args.no_merge)
    elif args.command == "remove":
        gdm.remove(args.name)
    elif args.command == "desparse":
        gdm.desparse(args.names)
    elif args.command == "status":
        gdm.status()
    elif args.command == "index":
        gdm.generate_index()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
