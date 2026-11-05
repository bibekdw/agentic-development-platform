# Read-only gh-aw source example

`issue-triage.md` is an example source workflow, deliberately NOT installed as an executable action. Before using it:
1. Install a currently supported `gh-aw` version and read its documentation.
2. Copy the Markdown file to `.github/workflows/issue-triage.md` in the **target** repository.
3. Run `gh aw compile` and inspect the generated `.lock.yml` (including permissions and tokens).
4. Run the workflow manually with least-privilege credentials.
5. Do not enable write capabilities until deterministic CI and branch protections exist.

Source inspiration: https://github.com/github/gh-aw and https://github.com/githubnext/agentics . No upstream source code is embedded.
