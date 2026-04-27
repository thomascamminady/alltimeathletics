# One-time GitHub setup

Run these once after pushing the initial commit. They enable the auto-merge
flow that keeps the data fresh without manual intervention.

## 1. Create the repo and push

```bash
gh repo create thomascamminady/alltimeathletics --public --source . --remote origin --push
```

## 2. Enable auto-merge on the repo

```bash
gh repo edit thomascamminady/alltimeathletics --enable-auto-merge
```

## 3. Enable GitHub Pages (source = Actions)

```bash
gh api -X POST /repos/thomascamminady/alltimeathletics/pages \
  -f 'build_type=workflow'
```

If that fails because Pages is already enabled, set the source manually in
**Settings → Pages → Source: GitHub Actions**.

## 4. Branch protection on `main` (required for auto-merge)

```bash
gh api -X PUT /repos/thomascamminady/alltimeathletics/branches/main/protection \
  --input - <<'EOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["test"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
```

The `test` check name comes from `.github/workflows/ci.yml`.

## 5. Trigger the first run manually

```bash
gh workflow run update-data.yml -R thomascamminady/alltimeathletics
gh workflow run deploy.yml -R thomascamminady/alltimeathletics
```

Watch:

```bash
gh run watch -R thomascamminady/alltimeathletics
```

After the deploy workflow finishes, the site is live at
<https://thomascamminady.github.io/alltimeathletics/>.
