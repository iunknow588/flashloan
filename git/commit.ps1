param(
    [string]$Message,
    [switch]$NoPush,
    [switch]$SkipSelfTest
)

# =============================================================================
# Config
# =============================================================================
$script:RepoRootOverride = ""
$script:RepoSshUrl = ""
$script:RepoHttpsUrl = ""
$script:PreferredRemoteUrl = ""
$script:CommitPathspec = "."

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-EnvFileValues {
    param(
        [string]$EnvPath
    )

    $values = @{}
    if (-not (Test-Path $EnvPath)) {
        return $values
    }

    foreach ($line in Get-Content -Path $EnvPath -ErrorAction SilentlyContinue) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }

        if ($trimmed -match '^(?<key>[^=]+?)\s*=\s*(?<value>.*)$') {
            $key = $Matches['key'].Trim()
            $value = $Matches['value'].Trim()
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            $values[$key] = $value
        }
    }

    return $values
}

function Load-RepoUrlsFromEnv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    $envPaths = @(
        (Join-Path $PSScriptRoot ".env"),
        (Join-Path $RepoRoot ".env")
    )

    foreach ($envPath in $envPaths | Select-Object -Unique) {
        if (-not (Test-Path $envPath)) {
            continue
        }

        $envValues = Get-EnvFileValues -EnvPath $envPath
        if ($envValues.ContainsKey("REPO_ROOT_OVERRIDE")) {
            $script:RepoRootOverride = $envValues["REPO_ROOT_OVERRIDE"]
        }
        if ($envValues.ContainsKey("REPO_SSH_URL")) {
            $script:RepoSshUrl = $envValues["REPO_SSH_URL"]
        }
        if ($envValues.ContainsKey("REPO_HTTPS_URL")) {
            $script:RepoHttpsUrl = $envValues["REPO_HTTPS_URL"]
        }
        if ($envValues.ContainsKey("PREFERRED_REMOTE_URL")) {
            $script:PreferredRemoteUrl = $envValues["PREFERRED_REMOTE_URL"]
        }
    }
}

# =============================================================================
# Helpers
# =============================================================================
function Resolve-RepoRoot {
    param(
        [switch]$CreateIfMissing
    )

    if ($RepoRootOverride) {
        if (-not (Test-Path $RepoRootOverride)) {
            throw "Configured RepoRootOverride does not exist: $RepoRootOverride"
        }
        return $RepoRootOverride
    }

    $defaultRoot = Split-Path -Parent $PSScriptRoot
    if ($defaultRoot -and (Test-Path (Join-Path $defaultRoot ".git"))) {
        return $defaultRoot
    }

    if ($CreateIfMissing) {
        if (-not $defaultRoot -or -not (Test-Path $defaultRoot)) {
            throw "Could not determine the default repository root from the script path."
        }
        return $defaultRoot
    }

    throw "Could not find a .git directory at the workspace root. Set `$RepoRootOverride or initialize a repository in the skill root first."
}

function Initialize-RepositoryIfMissing {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    if (Test-Path (Join-Path $Root ".git")) {
        return $false
    }

    Write-Host "No .git directory found. Initializing a repository in $Root" -ForegroundColor Yellow
    git -C $Root init | Out-Null
    git -C $Root branch -M main 2>$null | Out-Null
    return $true
}

function Get-OriginRemoteUrl {
    $remoteUrl = (git remote get-url origin 2>$null)
    if ($LASTEXITCODE -ne 0) {
        return ""
    }
    return $remoteUrl.Trim()
}

function Ensure-OriginRemote {
    if (-not $PreferredRemoteUrl) {
        $PreferredRemoteUrl = Get-OriginRemoteUrl
    }
    if (-not $PreferredRemoteUrl) {
        throw "No remote URL configured. Set PREFERRED_REMOTE_URL in scripts/git/.env or configure git origin first."
    }

    $remoteNames = @(git remote)
    $hasOrigin = $remoteNames -contains "origin"

    if ($hasOrigin) {
        git remote set-url origin $PreferredRemoteUrl
        git remote set-url --push origin $PreferredRemoteUrl
        Write-Host "Updated origin -> $PreferredRemoteUrl" -ForegroundColor Cyan
    } else {
        git remote add origin $PreferredRemoteUrl
        Write-Host "Added origin -> $PreferredRemoteUrl" -ForegroundColor Cyan
    }
}

function Get-BranchSyncState {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Branch
    )

    git fetch origin $Branch --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to fetch origin/$Branch before push."
    }

    $counts = git rev-list --left-right --count "origin/$Branch...$Branch"
    if ($LASTEXITCODE -ne 0 -or -not $counts) {
        throw "Failed to compare local and remote branch state for $Branch."
    }

    $parts = $counts.Trim() -split '\s+'
    if ($parts.Length -lt 2) {
        throw "Unexpected branch comparison result: $counts"
    }

    return @{
        Behind = [int]$parts[0]
        Ahead  = [int]$parts[1]
    }
}

function Invoke-SelfTests {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $packages = @(
        'skills/local-story-access',
        'skills/remote-gateway',
        'engine'
    )

    foreach ($package in $packages) {
        $packageRoot = Join-Path $Root $package
        if (-not (Test-Path (Join-Path $packageRoot 'package.json'))) {
            continue
        }
        Write-Host "Running selftest: $package" -ForegroundColor Cyan
        Push-Location $packageRoot
        try {
            npm run selftest
        } finally {
            Pop-Location
        }
    }
}

function Invoke-StageChanges {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Pathspec
    )

    git restore --staged -- .
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to clear staged changes before staging scoped pathspec."
    }

    $status = @(git status --porcelain -- $Pathspec)
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to inspect repository changes."
    }

    if ($status.Count -eq 0) {
        return
    }

    git add --all -- $Pathspec
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to stage repository changes under $Pathspec."
    }

    Remove-StagedIgnoredPathspecs
}

function Test-StagedChanges {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Pathspec
    )

    git diff --cached --quiet -- $Pathspec
    if ($LASTEXITCODE -eq 0) {
        return $false
    }
    if ($LASTEXITCODE -eq 1) {
        return $true
    }
    throw "Failed to inspect staged changes under $Pathspec."
}

function Remove-StagedIgnoredPathspecs {
    $ignoredPathspecs = @(
        '.vercel/**',
        'target/**',
        ':(glob)common/**/target/**',
        ':(glob)host/**/target/**',
        ':(glob)plugins/**/target/**',
        ':(glob)storylock/**/target/**',
        ':(glob)tools/**/target/**',
        ':(glob)yian-web/**/node_modules/**',
        ':(glob)**/node_modules/**'
    )

    foreach ($pathspec in $ignoredPathspecs) {
        $matches = @(git diff --cached --name-only -- $pathspec)
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to inspect staged ignored pathspec: $pathspec"
        }
        if ($matches.Count -eq 0) {
            continue
        }
        git restore --staged -- $pathspec
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to unstage ignored build or local files: $pathspec"
        }
    }
}

# =============================================================================
# Main
# =============================================================================
$root = Resolve-RepoRoot -CreateIfMissing
$root = [System.IO.Path]::GetFullPath($root)
Load-RepoUrlsFromEnv -RepoRoot $root
$repoCreated = Initialize-RepositoryIfMissing -Root $root
Set-Location $root
Ensure-OriginRemote

if ($repoCreated) {
    Write-Host "Repository initialized on branch main" -ForegroundColor Cyan
}

if (-not $SkipSelfTest) {
    Invoke-SelfTests -Root $root
}

$branch = (git branch --show-current).Trim()
if (-not $branch) {
    throw "Detached HEAD detected. Cannot auto-commit and push."
}

Invoke-StageChanges -Pathspec $CommitPathspec

if (Test-StagedChanges -Pathspec $CommitPathspec) {
    if (-not $Message) {
        $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        $Message = "Auto commit $timestamp"
    }

    git commit -m $Message
    Write-Host "Committed on branch $branch" -ForegroundColor Green
} else {
    Write-Host "No changes to commit" -ForegroundColor Yellow
}

if ($NoPush) {
    Write-Host "Skip push because -NoPush was specified" -ForegroundColor Yellow
} else {
    $syncState = Get-BranchSyncState -Branch $branch
    if ($syncState.Ahead -eq 0) {
        if ($syncState.Behind -gt 0) {
            Write-Host "Local branch is behind origin/$branch by $($syncState.Behind) commit(s). Nothing new to push." -ForegroundColor Yellow
            Write-Host "Run `git -C $root pull --rebase origin $branch` first." -ForegroundColor Yellow
            exit 0
        }

        Write-Host "Local branch is already up to date with origin/$branch. Nothing to push." -ForegroundColor Yellow
        exit 0
    }

    git push -u origin $branch
    if ($LASTEXITCODE -ne 0) {
        throw "Push failed. The remote branch is likely ahead. Run `git -C $root pull --rebase origin $branch` and retry."
    }
    Write-Host "Pushed to origin/$branch" -ForegroundColor Green
}
