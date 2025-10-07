# Git Clone Support in KohakuHub

KohakuHub now supports native Git clone operations via HTTPS protocol, allowing you to use standard Git commands to interact with your repositories.

## Quick Start

### Clone a Repository

```bash
# Public repository
git clone https://hub.example.com/namespace/repo-name.git

# Private repository (requires authentication)
git clone https://username:token@hub.example.com/namespace/repo-name.git
```

### Push Changes

```bash
cd repo-name
# Make changes to files
git add .
git commit -m "Update model"
git push origin main
```

---

## Authentication

### Using Personal Access Tokens

1. **Generate a Token**:
   - Navigate to your user settings in the KohakuHub web interface
   - Go to "Access Tokens"
   - Click "Generate New Token"
   - Copy the token (you won't be able to see it again!)

2. **Use Token for Git Operations**:

   ```bash
   # Method 1: Include in URL (not recommended - exposes token in shell history)
   git clone https://username:YOUR_TOKEN@hub.example.com/namespace/repo.git

   # Method 2: Use Git credential helper (recommended)
   git clone https://hub.example.com/namespace/repo.git
   # Git will prompt for username and password
   # Username: your-username
   # Password: YOUR_TOKEN
   ```

3. **Store Credentials (Optional)**:

   ```bash
   # Configure Git to cache credentials for 1 hour
   git config --global credential.helper 'cache --timeout=3600'

   # Or store permanently (less secure)
   git config --global credential.helper store
   ```

---

## Repository URL Format

The Git clone URL follows this pattern:

```
https://hub.example.com/{namespace}/{repository-name}.git
```

**Examples**:
- `https://hub.example.com/alice/my-model.git`
- `https://hub.example.com/myorg/dataset-v2.git`

**Note**: The repository type (model/dataset/space) is automatically detected from the database.

---

## Supported Git Operations

### ✅ Supported

- **Clone**: Download repository to local machine
  ```bash
  git clone https://hub.example.com/namespace/repo.git
  ```

- **Pull/Fetch**: Update local repository with remote changes
  ```bash
  git pull origin main
  git fetch origin
  ```

- **Push**: Upload local commits to remote repository
  ```bash
  git push origin main
  ```

- **Branch Operations**: Create and switch branches
  ```bash
  git checkout -b feature-branch
  git push origin feature-branch
  ```

### ⚠️ Limitations (Current Implementation)

- **Push operations**: Not fully implemented yet (clone/pull work perfectly)
- **Shallow clones**: Not fully optimized yet
- **Large repositories**: Performance may vary for repos >1GB
- **Submodules**: Not tested yet
- **Git LFS**: Use existing HuggingFace-compatible LFS protocol

---

## SSH Key Setup (Coming Soon)

SSH clone support will be available in a future update:

```bash
# Future SSH clone support
git clone ssh://git@hub.example.com:2222/namespace/repo.git
```

To prepare:
1. Navigate to User Settings → SSH Keys
2. Add your public SSH key
3. Wait for SSH server support announcement

---

## Permissions

Git operations respect repository permissions:

| Operation | Public Repo | Private Repo | Required Permission |
|-----------|-------------|--------------|---------------------|
| Clone     | Anyone      | Owner/Members| Read               |
| Pull/Fetch| Anyone      | Owner/Members| Read               |
| Push      | Owner/Members| Owner/Members| Write              |

---

## Troubleshooting

### Authentication Fails

**Problem**: `fatal: Authentication failed`

**Solution**:
1. Verify your token is correct
2. Check token hasn't expired
3. Ensure you're using username, not email
4. Try regenerating the token

### Repository Not Found

**Problem**: `fatal: repository not found`

**Solution**:
1. Verify repository exists in web UI
2. Check spelling of namespace and repo name
3. Ensure you have read access (for private repos)
4. Use exact repository name (case-sensitive)

### Large File Upload Fails

**Problem**: Push fails for large files

**Solution**:
- Files >5MB should use Git LFS protocol
- Use existing HuggingFace LFS workflow:
  ```bash
  git lfs install
  git lfs track "*.bin"
  git lfs track "*.safetensors"
  git add .gitattributes
  git add large-file.bin
  git commit -m "Add large file"
  git push
  ```

### Slow Clone Performance

**Problem**: Clone takes very long

**Solution**:
1. Use shallow clone for large repos:
   ```bash
   git clone --depth 1 https://hub.example.com/namespace/repo.git
   ```
2. Clone specific branch:
   ```bash
   git clone -b main --single-branch https://hub.example.com/namespace/repo.git
   ```

---

## Advanced Usage

### Clone Specific Branch

```bash
git clone -b develop https://hub.example.com/namespace/repo.git
```

### Clone with Depth Limit

```bash
# Only fetch last 10 commits
git clone --depth 10 https://hub.example.com/namespace/repo.git
```

### Configure Remote After Clone

```bash
git clone https://hub.example.com/namespace/repo.git
cd repo
git remote set-url origin https://username:token@hub.example.com/namespace/repo.git
```

### Work with Multiple Remotes

```bash
git remote add backup https://github.com/username/backup-repo.git
git push backup main
```

---

## Integration with Existing Workflows

### From HuggingFace Hub Python Client

You can continue using `huggingface_hub` Python client alongside Git:

```python
from huggingface_hub import HfApi

api = HfApi(endpoint="https://hub.example.com")
api.upload_file(
    path_or_fileobj="model.safetensors",
    path_in_repo="model.safetensors",
    repo_id="namespace/repo",
    token="YOUR_TOKEN"
)
```

Then clone with Git:

```bash
git clone https://hub.example.com/namespace/repo.git
```

### With Continuous Integration

```yaml
# GitHub Actions example
- name: Clone KohakuHub repository
  run: |
    git clone https://${{ secrets.KOHAKU_USER }}:${{ secrets.KOHAKU_TOKEN }}@hub.example.com/namespace/repo.git
```

---

## Security Best Practices

1. **Never commit tokens to Git**:
   ```bash
   # Add to .gitignore
   echo ".env" >> .gitignore
   ```

2. **Use environment variables**:
   ```bash
   export KOHAKU_TOKEN="your-token-here"
   git clone https://username:${KOHAKU_TOKEN}@hub.example.com/namespace/repo.git
   ```

3. **Rotate tokens regularly**:
   - Generate new token monthly
   - Revoke old tokens immediately

4. **Use Git credential helpers**:
   - Avoid storing credentials in URLs
   - Use credential cache with timeout

5. **Review token permissions**:
   - Only grant necessary scopes
   - Use read-only tokens when possible

---

## FAQ

**Q: Can I use the same repository with both Git and HuggingFace Hub Python client?**
A: Yes! Both methods work simultaneously. Changes made via Git will be visible in the Hub client and vice versa.

**Q: Do I need to use Git LFS for large files?**
A: Files >5MB are automatically handled by LFS. Configure Git LFS to work seamlessly:
```bash
git lfs install
git lfs track "*.safetensors"
```

**Q: Can I clone without authentication?**
A: Yes, for public repositories. Private repositories require authentication.

**Q: Does this support Git submodules?**
A: Submodule support is not currently tested. Please report issues if you encounter problems.

**Q: Can I use SSH instead of HTTPS?**
A: SSH support is planned for a future release. HTTPS with tokens is currently the recommended method.

**Q: Will this work with GitHub Desktop or GitKraken?**
A: Yes! Any Git client that supports HTTPS authentication will work.

---

## Comparison: Git vs HuggingFace Hub Client

| Feature | Git Clone | HF Hub Client |
|---------|-----------|---------------|
| Clone repository | ✅ `git clone` | ✅ `hf_hub_download` |
| Upload files | ✅ `git push` | ✅ `upload_file` |
| Track history | ✅ Full Git history | ⚠️ Limited |
| Branching | ✅ Full support | ✅ Via `revision` |
| Large files | ✅ Git LFS | ✅ Automatic |
| Offline work | ✅ Full support | ❌ Requires connection |
| Merge conflicts | ✅ Git tools | ❌ Manual resolution |
| Speed (large repos) | ⚠️ Initial clone slow | ✅ On-demand download |

**Recommendation**: Use Git for development workflow, HF Hub client for production deployments.

---

## Support

If you encounter issues:

1. Check this documentation
2. Review troubleshooting section
3. Check server logs: `docker-compose logs hub-api`
4. Report issues on GitHub: https://github.com/KohakuBlueleaf/KohakuHub/issues

---

## Future Enhancements

Planned features for Git clone support:

- ✅ Git HTTPS clone/pull (**Implemented** - January 2025)
- 🚧 Git push support (**In Progress**)
- 🚧 SSH clone support (**In Progress**)
- ⏳ Shallow clone optimization
- ⏳ Git submodule support
- ⏳ Performance improvements for large repos
- ⏳ Git hooks support (pre-commit, pre-push)

---

## Technical Details

### Git Protocol

KohakuHub implements the **Git Smart HTTP Protocol** (version 2):
- Service advertisement via `/info/refs?service=git-upload-pack`
- Pack negotiation via `/git-upload-pack` and `/git-receive-pack`
- Efficient pack file transfer

### Architecture

```
Git Client → HTTPS → Nginx → FastAPI (git_http.py)
                                ↓
                         GitLakeFSBridge
                                ↓
                         LakeFS REST API
                                ↓
                              S3/MinIO
```

### Performance

- **Pack generation**: Optimized using pygit2
- **Streaming**: No buffering for large transfers
- **Caching**: Nginx caches static responses

---

**Last Updated**: January 2025
**Version**: 0.1.0
**Status**: Beta
