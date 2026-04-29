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
    SUBMODULES_DIR = "repos"

    def __init__(self, root=None):
        self.root = Path(root or Path.cwd()).resolve()
        self.gdm_dir = self.root / self.GDM_DIR
        self.submodules_dir = self.gdm_dir / self.SUBMODULES_DIR
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
        if not self.submodules_dir.exists():
            self.submodules_dir.mkdir(parents=True)

        if not self.config_path.exists():
            default_config = {
                "shallow_depth": 1,
                "sparse_patterns": [
                    "/*",
                    "!/*",
                    "/docs",
                    "/Docs",
                    "/examples",
                    "/templates",
                    "/*.md*",
                    "/*.txt",
                    "/*.rst",
                ],
            }
            self._write_json(self.config_path, default_config)

        repos_file = self.gdm_dir / self.REPOS_FILE
        if not repos_file.exists():
            repos_file.touch()

        blacklist_file = self.gdm_dir / self.BLACKLIST_FILE
        if not blacklist_file.exists():
            blacklist_file.touch()

        whitelist_file = self.gdm_dir / self.WHITELIST_FILE
        if not whitelist_file.exists():
            whitelist_file.touch()

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

    def is_allowed(self, url_or_name):
        if any(item in url_or_name for item in self.blacklist):
            return False
        if self.whitelist:
            return any(item in url_or_name for item in self.whitelist)
        return True

    def _repo_name_from_url(self, url):
        return url.rstrip("/").split("/")[-1].replace(".git", "")

    def _detect_existing_submodules(self):
        discovered = {}
        if not self.submodules_dir.exists():
            return discovered

        for item in self.submodules_dir.iterdir():
            git_marker = item / ".git"
            if not item.is_dir():
                continue
            if not git_marker.exists() and not git_marker.is_file():
                continue

            res = self._run_git(["remote", "get-url", "origin"], cwd=item)
            if res.returncode == 0:
                remote_url = res.stdout.strip()
                discovered[item.name] = remote_url
            else:
                discovered[item.name] = None

        return discovered

    def _get_tracked_submodules(self):
        res = self._run_git(["submodule", "status", "--recursive"], cwd=self.submodules_dir)
        tracked = {}
        if res.returncode != 0:
            return tracked
        for line in res.stdout.strip().splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                sha = parts[0].lstrip("+-")
                path = parts[1]
                tracked[path] = {"sha": sha, "modified": parts[0].startswith("+"), "outdated": parts[0].startswith("-")}
        return tracked

    def add(self, url, sparse=None, depth=None):
        if not self.is_allowed(url):
            print(f"Blocked: {url} is not allowed (blacklisted or not whitelisted)")
            return

        name = self._repo_name_from_url(url)
        repo_path = self.submodules_dir / name

        if repo_path.exists():
            print(f"Already exists: {name}. Use 'sync' to update.")
            return

        effective_depth = depth or self.shallow_depth

        print(f"Adding {name} (depth={effective_depth})")
        res = self._run_git(
            ["submodule", "add", "--depth", str(effective_depth), url, f"./{self.SUBMODULES_DIR}/{name}"],
            cwd=self.root,
        )
        if res.returncode != 0:
            print(f"submodule add failed, falling back to clone: {res.stderr.strip()}")
            self._run_git(
                ["clone", "--no-checkout", "--depth", str(effective_depth), url, str(repo_path)]
            )
            self._run_git(["init"], cwd=repo_path, check=True)

        self._apply_sparse_checkout(repo_path, sparse)
        self._run_git(["checkout", "HEAD"], cwd=repo_path)

        if url not in self.target_repos:
            self._append_file(self.gdm_dir / self.REPOS_FILE, url)
            self.target_repos.append(url)

        print(f"Added: {name}")

    def remove(self, name):
        repo_path = self.submodules_dir / name
        if not repo_path.exists():
            print(f"Not found: {name}")
            return

        res = self._run_git(
            ["submodule", "deinit", "-f", f"./{self.SUBMODULES_DIR}/{name}"],
            cwd=self.root,
        )
        self._run_git(["rm", "-rf", str(repo_path)], cwd=self.root)

        if res.returncode != 0:
            import shutil
            shutil.rmtree(repo_path, ignore_errors=True)

        self.target_repos = [u for u in self.target_repos if self._repo_name_from_url(u) != name]
        self._write_file(self.gdm_dir / self.REPOS_FILE, "\n".join(self.target_repos) + "\n" if self.target_repos else "")

        print(f"Removed: {name}")

    def _apply_sparse_checkout(self, repo_path, patterns=None):
        effective_patterns = patterns or self.sparse_patterns
        if not effective_patterns:
            return

        self._run_git(["sparse-checkout", "init", "--cone"], cwd=repo_path)
        self._run_git(["sparse-checkout", "set"] + effective_patterns, cwd=repo_path)

        sparse_file = repo_path / ".git" / "info" / "sparse-checkout"
        git_modules = repo_path / ".git"
        if git_modules.is_file():
            git_dir = Path(git_modules.read_text().strip().split(":")[-1].strip())
            sparse_file = git_dir / "info" / "sparse-checkout"

        if not sparse_file.parent.exists():
            sparse_file.parent.mkdir(parents=True, exist_ok=True)
        sparse_file.write_text("\n".join(effective_patterns) + "\n")

    def sync(self):
        existing = self._detect_existing_submodules()
        tracked = self._get_tracked_submodules()

        print(f"Detected {len(existing)} existing submodules, {len(tracked)} tracked by git")

        for url in self.target_repos:
            name = self._repo_name_from_url(url)

            if not self.is_allowed(url) and not self.is_allowed(name):
                print(f"Skipping {name} (not allowed)")
                continue

            repo_path = self.submodules_dir / name

            if not repo_path.exists():
                print(f"Cloning: {name}")
                effective_depth = self.shallow_depth
                self._run_git(
                    ["clone", "--no-checkout", "--depth", str(effective_depth), url, str(repo_path)]
                )
            else:
                print(f"Updating: {name}")
                if name in tracked:
                    self._run_git(["submodule", "update", "--remote", f"./{self.SUBMODULES_DIR}/{name}"], cwd=self.root)
                else:
                    self._run_git(["pull", "--depth", str(self.shallow_depth)], cwd=repo_path)

            self._apply_sparse_checkout(repo_path)
            self._run_git(["checkout", "HEAD"], cwd=repo_path)

        for name, remote_url in existing.items():
            if not self.is_allowed(name):
                continue
            if remote_url and remote_url not in self.target_repos:
                print(f"Registering discovered submodule: {name} -> {remote_url}")
                self._append_file(self.gdm_dir / self.REPOS_FILE, remote_url)
                self.target_repos.append(remote_url)

        self.generate_index()

    def status(self):
        existing = self._detect_existing_submodules()
        tracked = self._get_tracked_submodules()

        print(f"\nGDM Status ({self.root})")
        print(f"{'='*50}")
        print(f"Configured repos: {len(self.target_repos)}")
        print(f"Detected submodules: {len(existing)}")
        print(f"Git-tracked submodules: {len(tracked)}")
        print(f"Shallow depth: {self.shallow_depth}")
        print(f"Sparse patterns: {len(self.sparse_patterns)}")

        if existing:
            print(f"\n{'Name':<30} {'Remote':<50} {'Status'}")
            print(f"{'-'*30} {'-'*50} {'-'*20}")
            for name, remote_url in sorted(existing.items()):
                status = "tracked" if name in tracked else "untracked"
                if name in tracked:
                    info = tracked[name]
                    if info.get("modified"):
                        status = "modified"
                    elif info.get("outdated"):
                        status = "outdated"
                print(f"{name:<30} {(remote_url or 'unknown'):<50} {status}")

        unregistered = [u for u in self.target_repos if self._repo_name_from_url(u) not in existing]
        if unregistered:
            print(f"\nNot yet cloned:")
            for url in unregistered:
                print(f"  {self._repo_name_from_url(url)}: {url}")

    def _write_file(self, path, content):
        with open(path, "w") as f:
            f.write(content)

    def generate_index(self):
        manifest = {}
        for repo_dir in self.submodules_dir.iterdir():
            if not repo_dir.is_dir() or repo_dir.name.startswith("."):
                continue
            if not self.is_allowed(repo_dir.name):
                continue

            git_marker = repo_dir / ".git"
            if not git_marker.exists() and not git_marker.is_file():
                continue

            manifest[repo_dir.name] = sorted(
                str(p.relative_to(repo_dir))
                for p in repo_dir.rglob("*")
                if p.is_file() and ".git" not in p.parts
            )

        index_path = self.gdm_dir / self.INDEX_FILE
        self._write_json(index_path, {"graph": manifest, "root": str(self.root), "shallow_depth": self.shallow_depth})
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

    add_parser = sub.add_parser("add", help="Add a new submodule")
    add_parser.add_argument("url", help="Repository URL to add")
    add_parser.add_argument("--sparse", nargs="*", default=None, help="Sparse checkout patterns (overrides config)")
    add_parser.add_argument("--depth", type=int, default=None, help="Override shallow depth for this repo")

    remove_parser = sub.add_parser("remove", help="Remove a submodule")
    remove_parser.add_argument("name", help="Repository name to remove")

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
        gdm.sync()
    elif args.command == "add":
        gdm.add(args.url, sparse=args.sparse, depth=args.depth)
    elif args.command == "remove":
        gdm.remove(args.name)
    elif args.command == "status":
        gdm.status()
    elif args.command == "index":
        gdm.generate_index()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
