import os
import subprocess
import json
from pathlib import Path

class GitDocsManager:
    def __init__(self, repos_file="repos.txt", template_file="sparse-template.txt"):
        self.base_dir = Path.cwd() / "gitdocs"
        
        # Paths for new filter files
        self.files = {
            "repos": Path(repos_file),
            "template": Path(template_file),
            "blacklist": Path("blacklist.txt"),
            "whitelist": Path("whitelist.txt")
        }
        
        self._ensure_files_exist()
        
        # Load all configurations
        self.target_repos = self._load_file("repos")
        self.template = self._load_file("template")
        self.blacklist = self._load_file("blacklist")
        self.whitelist = self._load_file("whitelist")
            
        self._init_main_repo()

    def _ensure_files_exist(self):
        for p in self.files.values():
            if not p.exists():
                p.touch()

    def _load_file(self, key):
        with open(self.files[key], "r") as f:
            return [line.strip() for line in f if line.strip() and not line.startswith("#")]

    def _init_main_repo(self):
        if not self.base_dir.exists():
            self.base_dir.mkdir(parents=True)
        os.chdir(self.base_dir)
        if not (self.base_dir / ".git").exists():
            subprocess.run(["git", "init"], check=True)

    def _run_git(self, args, cwd=None):
        return subprocess.run(["git"] + args, cwd=cwd or self.base_dir, capture_output=True, text=True)

    def is_allowed(self, url_or_name):
        """Checks if a repo is permitted based on whitelist/blacklist."""
        # Check blacklist first
        if any(item in url_or_name for item in self.blacklist):
            return False
        
        # If whitelist is active, only allow matches
        if self.whitelist:
            return any(item in url_or_name for item in self.whitelist)
        
        return True

    def sync(self):
        # 1. Process explicit targets
        for url in self.target_repos:
            name = url.split("/")[-1].replace(".git", "")
            
            if not self.is_allowed(url) and not self.is_allowed(name):
                print(f"Skipping {name} (Blacklisted or not in Whitelist)")
                continue

            repo_path = self.base_dir / name
            if not repo_path.exists():
                print(f"Cloning: {name}")
                self._run_git(["clone", "--no-checkout", "--depth", "1", url, str(repo_path)])
                self._run_git(["submodule", "add", url, f"./{name}"])
            
            # Apply template & Update
            self._run_git(["sparse-checkout", "set"] + self.template, cwd=repo_path)
            self._run_git(["checkout", "HEAD"], cwd=repo_path)
            self._run_git(["pull", "origin", "main"], cwd=repo_path)

        # 2. Discovery with Filtering
        for item in self.base_dir.iterdir():
            if item.is_dir() and ((item / ".git").exists() or (item / ".git").is_file()):
                if not self.is_allowed(item.name):
                    continue
                
                # Get remote to see if we should add it to repos.txt
                res = self._run_git(["remote", "get-url", "origin"], cwd=item)
                remote_url = res.stdout.strip() if res.returncode == 0 else None
                
                if remote_url and remote_url not in self.target_repos:
                    print(f"Discovered new allowed repo: {item.name}. Updating repos.txt.")
                    with open(self.files["repos"], "a") as f:
                        f.write(f"\n{remote_url}")
                    self.target_repos.append(remote_url)

        self.generate_index()

    def generate_index(self):
        """Builds the final directory graph."""
        manifest = {}
        for repo_dir in self.base_dir.iterdir():
            if repo_dir.is_dir() and not repo_dir.name.startswith('.'):
                # Ensure we only index what is allowed
                if not self.is_allowed(repo_dir.name):
                    continue
                    
                manifest[repo_dir.name] = [
                    str(p.relative_to(repo_dir)) 
                    for p in repo_dir.rglob('*') 
                    if p.is_file() and ".git" not in p.parts
                ]

        with open(self.base_dir / "index.json", "w") as f:
            json.dump({"graph": manifest}, f, indent=4)
        print("\nSync complete. Filters applied and Index updated.")

if __name__ == "__main__":
    GitDocsManager().sync()