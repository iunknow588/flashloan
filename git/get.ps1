param(
    [switch]$Rebase,
    [switch]$AllowDirty
)

# =============================================================================
# Config
# =============================================================================
$script:RepoRootOverride = ""
$script:RepoSshUrl = ""
$script:RepoHttpsUrl = ""
$script:PreferredRemoteUrl = ""

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
    if ($RepoRootOverride) {
        if (-not (Test-Path $RepoRootOverride)) {
            throw "Configured RepoRootOverride does not exist: $RepoRootOverride"
        }
        return $RepoRootOverride
    }

    $candidate = [System.IO.DirectoryInfo]::new($PSScriptRoot)
    while ($candidate) {
        if (Test-Path (Join-Path $candidate.FullName ".git")) {
            return $candidate.FullName
        }
        $candidate = $candidate.Parent
    }

    return (Split-Path -Parent $PSScriptRoot)
}

function Ensure-GitRepository {
    if (Test-Path ".git") {
        return
    }

    if (-not $PreferredRemoteUrl) {
        throw "No .git directory found and no remote URL configured. Set PREFERRED_REMOTE_URL in git/.env."
    }

    Write-Host "No .git directory found. Initializing repository at $root" -ForegroundColor Yellow
    git init
}

function Ensure-OriginRemote {
    if (-not $PreferredRemoteUrl) {
        $PreferredRemoteUrl = (git remote get-url origin 2>$null).Trim()
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

function Get-CurrentOrDefaultBranch {
    $branch = (git branch --show-current).Trim()
    $hasHead = $false
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        git rev-parse --verify HEAD *> $null
        if ($LASTEXITCODE -eq 0) {
            $hasHead = $true
        }
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($branch -and $hasHead) {
        return $branch
    }

    git fetch origin
    $originHead = (git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>$null).Trim()
    if ($originHead -and $originHead.StartsWith("origin/")) {
        return $originHead.Substring("origin/".Length)
    }

    $remoteBranches = @(git branch -r --format "%(refname:short)")
    if ($remoteBranches -contains "origin/main") {
        return "main"
    }
    if ($remoteBranches -contains "origin/master") {
        return "master"
    }

    throw "Detached HEAD or unborn branch detected, and could not determine origin's default branch."
}

function Test-HeadExists {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        git rev-parse --verify HEAD *> $null
        return $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Ensure-LocalBranchName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Branch
    )

    $current = (git branch --show-current).Trim()
    if (-not $current -or $current -eq $Branch) {
        return
    }

    if (Test-HeadExists) {
        return
    }

    git branch -m $Branch
    Write-Host "Renamed unborn branch $current -> $Branch" -ForegroundColor Cyan
}

# =============================================================================
# Main
# =============================================================================
$scriptEnvPath = Join-Path $PSScriptRoot ".env"
if (Test-Path $scriptEnvPath) {
    $envValues = Get-EnvFileValues -EnvPath $scriptEnvPath
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

$root = Resolve-RepoRoot
$root = [System.IO.Path]::GetFullPath($root)
Load-RepoUrlsFromEnv -RepoRoot $root
Set-Location $root
Ensure-GitRepository
Ensure-OriginRemote

$branch = Get-CurrentOrDefaultBranch
Ensure-LocalBranchName -Branch $branch

$status = git status --porcelain
if ($status -and -not $AllowDirty) {
    throw "Working tree is dirty. Commit/stash changes first, or rerun with -AllowDirty."
}

if ($Rebase) {
    git pull --rebase origin $branch
} else {
    git pull --ff-only origin $branch
}

Write-Host "Updated branch $branch" -ForegroundColor Green
