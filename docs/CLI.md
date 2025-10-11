# KohakuHub CLI Design Document

*Last Updated: January 2025*

## Quick Reference

```bash
# Authentication
kohub-cli auth login                        # Login to KohakuHub
kohub-cli auth token create --name "name"   # Create API token

# Repositories
kohub-cli repo create REPO_ID --type TYPE   # Create repository
kohub-cli repo list --type TYPE             # List repositories
kohub-cli repo ls NAMESPACE                 # List namespace repos
kohub-cli repo files REPO_ID                # List repository files

# Organizations
kohub-cli org create NAME                   # Create organization
kohub-cli org member add ORG USER --role R  # Add member

# Settings & Management
kohub-cli settings repo update REPO_ID --private        # Update repo settings
kohub-cli settings repo move FROM TO --type TYPE        # Move/rename repo
kohub-cli settings repo branch create REPO_ID BRANCH    # Create branch
kohub-cli settings repo tag create REPO_ID TAG          # Create tag
kohub-cli settings organization members ORG             # List org members

# Configuration
kohub-cli config set KEY VALUE              # Set config value
kohub-cli config list                       # Show all config

# Interactive Mode
kohub-cli interactive                       # Launch TUI mode
```

## Overview

The KohakuHub CLI (`kohub-cli`) provides both a **Python API** for programmatic access and a **command-line interface** for interactive and scripted usage. This design aims to make it easy to integrate KohakuHub into existing workflows while maintaining compatibility with HuggingFace ecosystem patterns.

## Design Goals

1. **Python API First**: Expose all functionality through a clean Python API (similar to `huggingface_hub.HfApi`)
2. **CLI as a Wrapper**: Build CLI commands on top of the Python API
3. **Dual Mode**: Support both interactive (TUI) and non-interactive (scripted) modes
4. **Configuration Management**: Easy endpoint and credential management
5. **HuggingFace Compatibility**: Similar patterns and naming conventions where applicable
6. **Extensibility**: Easy to add new features without breaking existing code

## Architecture

```mermaid
graph TD
    A[CLI Interface] --> B[Python API]
    B --> C[HTTP Client]
    C --> D[KohakuHub REST API]
```

## Python API Design

### Core Class: `KohubClient`

```python
from kohub_cli import KohubClient

# Initialize client
client = KohubClient(
    endpoint="http://localhost:28080",
    token="your_token_here"  # optional
)

# Or use environment variables
# HF_ENDPOINT, HF_TOKEN
client = KohubClient()
```

### User Operations

```python
# Register a new user
client.register(username="alice", email="alice@example.com", password="secret")

# Login (creates session)
session_info = client.login(username="alice", password="secret")

# Get current user info
user_info = client.whoami()

# Logout
client.logout()
```

### Token Management

```python
# Create an API token
token_info = client.create_token(name="my-laptop")
# Returns: {"token": "hf_...", "token_id": 123, ...}

# List all tokens
tokens = client.list_tokens()

# Revoke a token
client.revoke_token(token_id=123)
```

### Organization Operations

```python
# Create organization
client.create_organization(name="my-org", description="My awesome org")

# Get organization info
org = client.get_organization("my-org")

# List user's organizations
orgs = client.list_user_organizations(username="alice")

# List organization members
members = client.list_organization_members("my-org")

# Add member to organization
client.add_organization_member(
    org_name="my-org",
    username="bob",
    role="member"  # or "admin", "super-admin"
)

# Update member role
client.update_organization_member(
    org_name="my-org",
    username="bob",
    role="admin"
)

# Remove member
client.remove_organization_member(org_name="my-org", username="bob")

# Update organization settings
client.update_organization_settings(
    org_name="my-org",
    description="Updated description"
)
```

### Repository Operations

```python
# Create repository
repo = client.create_repo(
    repo_id="my-org/my-model",
    repo_type="model",  # "model", "dataset", or "space"
    private=False
)

# Delete repository
client.delete_repo(repo_id="my-org/my-model", repo_type="model")

# Get repository info
info = client.repo_info(
    repo_id="my-org/my-model",
    repo_type="model",
    revision="main"  # optional
)

# List repository files
files = client.list_repo_tree(
    repo_id="my-org/my-model",
    repo_type="model",
    revision="main",
    path="",
    recursive=False
)

# List all repositories of a type
repos = client.list_repos(repo_type="model", author="my-org", limit=50)

# List repositories under a namespace
repos = client.list_namespace_repos(
    namespace="my-org",
    repo_type="model"  # optional
)

# Update repository settings
client.update_repo_settings(
    repo_id="my-org/my-model",
    repo_type="model",
    private=True,
    gated="auto"  # "auto", "manual", or None
)

# Move/rename repository
client.move_repo(
    from_repo="my-org/old-name",
    to_repo="my-org/new-name",
    repo_type="model"
)

# Create branch
client.create_branch(
    repo_id="my-org/my-model",
    branch="dev",
    repo_type="model",
    revision="main"  # optional source revision
)

# Delete branch
client.delete_branch(
    repo_id="my-org/my-model",
    branch="dev",
    repo_type="model"
)

# Create tag
client.create_tag(
    repo_id="my-org/my-model",
    tag="v1.0",
    repo_type="model",
    revision="main",  # optional
    message="Release v1.0"  # optional
)

# Delete tag
client.delete_tag(
    repo_id="my-org/my-model",
    tag="v1.0",
    repo_type="model"
)

# Update user settings
client.update_user_settings(
    username="alice",
    email="newemail@example.com"
)
```

### Configuration

```python
# Save configuration
client.save_config(
    endpoint="http://localhost:28080",
    token="hf_..."
)

# Load configuration
config = client.load_config()

# Get config file path
path = client.config_path  # ~/.kohub/config.json
```

## CLI Design

### Command Structure

```
kohub-cli
├── auth
│   ├── login           # Login with username/password
│   ├── logout          # Logout current session
│   ├── whoami          # Show current user
│   └── token
│       ├── create      # Create new API token
│       ├── list        # List all tokens
│       └── delete      # Delete a token
├── repo
│   ├── create          # Create repository
│   ├── delete          # Delete repository
│   ├── info            # Show repository info
│   ├── list            # List repositories
│   ├── ls              # List repositories under a namespace
│   └── files           # List repository files
├── org
│   ├── create          # Create organization
│   ├── info            # Show organization info
│   ├── list            # List user's organizations
│   └── member
│       ├── add         # Add member to org
│       ├── remove      # Remove member from org
│       └── update      # Update member role
├── settings
│   ├── user
│   │   └── update      # Update user settings
│   ├── repo
│   │   ├── update      # Update repository settings
│   │   ├── move        # Move/rename repository
│   │   ├── branch
│   │   │   ├── create  # Create branch
│   │   │   └── delete  # Delete branch
│   │   └── tag
│   │       ├── create  # Create tag
│   │       └── delete  # Delete tag
│   └── organization
│       ├── update      # Update organization settings
│       └── members     # List organization members
├── config
│   ├── set             # Set configuration value
│   ├── get             # Get configuration value
│   ├── list            # Show all configuration
│   └── clear           # Clear all configuration
└── interactive         # Launch interactive TUI mode
```

### Command Examples

#### Authentication

```bash
# Login
kohub-cli auth login
kohub-cli auth login --username alice --password secret

# Logout
kohub-cli auth logout

# Check current user
kohub-cli auth whoami

# Create token
kohub-cli auth token create --name "my-laptop"
kohub-cli auth token create -n "my-laptop"

# List tokens
kohub-cli auth token list

# Delete token
kohub-cli auth token delete --id 123
```

#### Repository Operations

```bash
# Create repository
kohub-cli repo create my-model --type model
kohub-cli repo create my-org/my-model --type model --private

# Delete repository
kohub-cli repo delete my-org/my-model --type model

# Show repository info
kohub-cli repo info my-org/my-model --type model
kohub-cli repo info my-org/my-model --type model --revision v1.0

# List repositories
kohub-cli repo list --type model
kohub-cli repo list --type model --author my-org --limit 100

# List repositories under a namespace
kohub-cli repo ls my-org
kohub-cli repo ls my-org --type model

# List files in repository
kohub-cli repo files my-org/my-model
kohub-cli repo files my-org/my-model --revision main --path configs/ --recursive
```

#### Organization Operations

```bash
# Create organization
kohub-cli org create my-org --description "My organization"

# Show organization info
kohub-cli org info my-org

# List user's organizations
kohub-cli org list
kohub-cli org list --username alice

# Add member
kohub-cli org member add my-org bob --role member

# Update member role
kohub-cli org member update my-org bob --role admin

# Remove member
kohub-cli org member remove my-org bob
```

#### Settings Operations

```bash
# Update user settings
kohub-cli settings user update --email newemail@example.com

# Update repository settings
kohub-cli settings repo update my-org/my-model --type model --private
kohub-cli settings repo update my-org/my-model --type model --public
kohub-cli settings repo update my-org/my-model --type model --gated auto

# Move/rename repository
kohub-cli settings repo move my-org/old-name my-org/new-name --type model
kohub-cli settings repo move my-user/my-model my-org/my-model --type model

# Create branch
kohub-cli settings repo branch create my-org/my-model dev --type model
kohub-cli settings repo branch create my-org/my-model feature-x --type model --revision main

# Delete branch
kohub-cli settings repo branch delete my-org/my-model dev --type model

# Create tag
kohub-cli settings repo tag create my-org/my-model v1.0 --type model
kohub-cli settings repo tag create my-org/my-model v1.0 --type model --revision main --message "Release v1.0"

# Delete tag
kohub-cli settings repo tag delete my-org/my-model v1.0 --type model

# Update organization settings
kohub-cli settings organization update my-org --description "New description"

# List organization members
kohub-cli settings organization members my-org
```

#### Configuration

```bash
# Set endpoint
kohub-cli config set endpoint http://localhost:28080

# Set token
kohub-cli config set token hf_...

# Get current endpoint
kohub-cli config get endpoint

# Show all configuration
kohub-cli config list

# Clear configuration
kohub-cli config clear
```

#### Interactive Mode

```bash
# Launch interactive TUI (current behavior)
kohub-cli interactive

# Or just run without arguments
kohub-cli
```

### Global Options

All commands support these global options:

```bash
--endpoint URL          # Override endpoint (or use HF_ENDPOINT env var)
--token TOKEN           # Override token (or use HF_TOKEN env var)
--output {json,text}    # Output format (default: text)
--help                  # Show help
```

Examples:
```bash
kohub-cli --endpoint http://localhost:28080 auth whoami
kohub-cli --output json repo list --type model
kohub-cli --token hf_xxxxx repo create my-model --type model
```

## Configuration File Format

Located at `~/.kohub/config.json`:

```json
{
  "endpoint": "http://localhost:28080",
  "token": "hf_...",
  "default_repo_type": "model",
  "interactive_mode_default": true
}
```

## Error Handling

### Python API

```python
from kohub_cli import KohubClient, KohubError, AuthenticationError, NotFoundError

client = KohubClient()

try:
    client.create_repo("my-repo", repo_type="model")
except AuthenticationError:
    print("Please login first")
except NotFoundError as e:
    print(f"Not found: {e}")
except KohubError as e:
    print(f"Error: {e}")
```

### CLI

```bash
$ kohub-cli repo create my-repo --type model
Error: Authentication required. Please login with 'kohub-cli auth login'

$ kohub-cli repo info nonexistent/repo --type model
Error: Repository not found: nonexistent/repo

$ kohub-cli --output json repo info nonexistent/repo --type model
{"error": "Repository not found", "code": "RepoNotFound", "details": "nonexistent/repo"}
```

## Environment Variables

- `HF_ENDPOINT` - KohakuHub endpoint URL (default: `http://localhost:28080`)
- `HF_TOKEN` - API token for authentication
- `KOHUB_CONFIG_DIR` - Config directory (default: `~/.kohub`)

## Migration from Current Implementation

### Compatibility

1. Keep existing `main_menu()` function for backward compatibility
2. Make it accessible via `kohub-cli interactive`
3. If no arguments provided, launch interactive mode (current behavior)

### Deprecation Path

1. v0.1.x - Add new API and CLI commands alongside interactive mode
2. v0.2.x - Recommend new CLI commands in help text
3. v0.3.x - Default to CLI commands, require `--interactive` for TUI

## Implementation Phases

### Phase 1: Python API (Priority: High) ✅ COMPLETED
- [x] Create `KohubClient` class
- [x] Implement user operations
- [x] Implement token management
- [x] Implement organization operations
- [x] Implement repository operations
- [x] Add proper error classes
- [x] Add configuration management

### Phase 2: CLI Commands (Priority: High) ✅ COMPLETED
- [x] Set up Click command structure
- [x] Implement `auth` commands
- [x] Implement `repo` commands
- [x] Implement `org` commands
- [x] Implement `config` commands
- [x] Implement `settings` commands (user, repo, organization)
- [x] Add global options support
- [x] Add output formatting (JSON/text)
- [x] Implement branch/tag management
- [x] Rich output formatting with tables

### Phase 3: Enhanced Features (Priority: Medium)
- [ ] Bash/Zsh completion scripts
- [ ] Progress bars for long operations
- [x] Rich output formatting with tables (partial - implemented for lists)
- [ ] Configuration wizard
- [ ] Batch operations support

### Phase 4: Advanced Features (Priority: Low)
- [ ] Plugin system
- [ ] Alias support
- [ ] History and undo
- [ ] Integration with git
- [ ] File upload/download commands (if needed beyond hfutils)

## Testing Strategy

### Unit Tests
```python
def test_create_repo():
    client = KohubClient(endpoint="http://test", token="test_token")
    with mock_http_response(200, {"repo_id": "test/repo"}):
        result = client.create_repo("test/repo", repo_type="model")
        assert result["repo_id"] == "test/repo"
```

### Integration Tests
```bash
# Test CLI commands
kohub-cli --endpoint http://test-server:8000 auth login --username test --password test
kohub-cli repo create test-repo --type model
kohub-cli repo info test-repo --type model
kohub-cli repo delete test-repo --type model
```

## Documentation Requirements

1. **API Reference** - Auto-generated from docstrings
2. **CLI Reference** - Auto-generated from Click commands
3. **Tutorials** - Getting started, common workflows
4. **Examples** - Python scripts and shell scripts

## Success Metrics

1. **Usability**: 90% of operations possible via CLI without interactive mode
2. **API Coverage**: 100% of HTTP endpoints wrapped in Python API
3. **Documentation**: Every function/command has examples
4. **Testing**: >80% code coverage
5. **Performance**: CLI commands respond in <1s for metadata operations

## Future Considerations

1. **Async Support**: `AsyncKohubClient` for async Python applications
2. **Streaming**: Progress callbacks for large operations
3. **Caching**: Cache repo metadata locally
4. **Webhooks**: CLI support for webhook management
5. **Desktop App**: Electron wrapper for non-technical users
