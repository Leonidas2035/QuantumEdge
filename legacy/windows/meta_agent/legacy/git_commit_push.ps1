param(
    [string]$Message = ""
)

# Go to script directory (repo root)
Set-Location -Path $PSScriptRoot

# Check if git exists
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Git is not installed or not in PATH."
    exit 1
}

Write-Host "Repository path: $(Get-Location)`n"

# Show status
Write-Host "Checking git status..."
git status

# Detect changes
$changes = git status --porcelain
if (-not $changes) {
    Write-Host "`nNo changes to commit. Exiting."
    exit 0
}

# Commit message
if ([string]::IsNullOrWhiteSpace($Message)) {
    $Message = Read-Host "`nEnter commit message"
}

if ([string]::IsNullOrWhiteSpace($Message)) {
    Write-Host "Commit message cannot be empty. Exiting."
    exit 1
}

# Add files
Write-Host "`nAdding files (git add .)..."
git add .

Write-Host "`nUpdated git status:"
git status

# Confirmation
$confirm = Read-Host "`nProceed with commit and push? (y/N)"
if ($confirm -ne "y" -and $confirm -ne "Y") {
    Write-Host "Operation cancelled."
    exit 0
}

# Commit
Write-Host "`nCommitting..."
git commit -m "$Message"

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Commit failed."
    exit $LASTEXITCODE
}

# Detect branch
$currentBranch = git rev-parse --abbrev-ref HEAD
Write-Host "`nCurrent branch: $currentBranch"

# Push
Write-Host "Pushing to origin/$currentBranch ..."
git push origin $currentBranch

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nSUCCESS: Changes committed and pushed."
} else {
    Write-Host "`nERROR: Push failed."
}
