# Publishing the starter

From the **parent workspace** containing five sibling repository folders, install GitHub CLI and log in:

```bash
gh auth login
gh auth status
PUBLISH=YES OWNER=bibekdw bash ./publish-to-github.sh
```

The script creates five *public* repositories and pushes an initial `main` commit to each. It refuses to proceed if a repository already exists or if a local folder has an existing Git history/remote, to avoid overwriting content. **Review all source files before publishing.**

GitHub API permissions exposed to the assistant do not include creating repositories; this script must be executed by the repository owner in an authenticated local environment. Never paste tokens into chat.

After publishing, configure branch protection, CODEOWNERS and required CI checks manually or via approved authenticated tooling. Then update any hard-coded sample owner values if using a GitHub account other than `bibekdw`.

On Windows PowerShell, run instead:

```powershell
$env:OWNER='bibekdw'
$env:PUBLISH='YES'
.\publish-to-github.ps1
```
