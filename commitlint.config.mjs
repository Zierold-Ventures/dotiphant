// Commit messages follow Conventional Commits: https://www.conventionalcommits.org
// Format: type(scope): subject. See CONTRIBUTING.md.
export default {
  extends: ["@commitlint/config-conventional"],
  rules: {
    "type-enum": [
      2,
      "always",
      ["feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert"],
    ],
    // Unknown scopes only warn, so a new area does not block a commit.
    "scope-enum": [
      1,
      "always",
      ["app", "site", "sync", "mcp", "cli", "importer", "oauth", "docs", "release", "deps", "ci", "brand"],
    ],
    "header-max-length": [2, "always", 100],
    // Bodies and footers are welcome and may hold long lines such as URLs.
    "body-max-line-length": [0],
    "footer-max-line-length": [0],
  },
};
