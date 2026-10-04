$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$userProfilePath = if ($env:USERPROFILE) { $env:USERPROFILE } else { [Environment]::GetFolderPath('UserProfile') }
$bundledPython = Join-Path $userProfilePath '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue

$pythonPath = if ($env:SKILLSENTRA_PYTHON -and (Test-Path -LiteralPath $env:SKILLSENTRA_PYTHON)) {
    $env:SKILLSENTRA_PYTHON
}
elseif (Test-Path -LiteralPath $bundledPython) {
    $bundledPython
}
elseif ($pythonCommand -and (Test-Path -LiteralPath $pythonCommand.Source)) {
    $pythonCommand.Source
}
else {
    throw 'Python 3.11 or newer was not found. Set SKILLSENTRA_PYTHON to the executable path.'
}

Push-Location -LiteralPath $projectRoot
try {
    if (-not $env:SKILLSENTRA_DEMO_SEED) { $env:SKILLSENTRA_DEMO_SEED = 'true' }
    # GitHub API handshakes on the local network can exceed five seconds. Keep
    # the bounded request below the browser's 12-second timeout while allowing
    # one bounded retry for transient failures.
    if (-not $env:SKILLSENTRA_GITHUB_TIMEOUT_SECONDS) { $env:SKILLSENTRA_GITHUB_TIMEOUT_SECONDS = '10' }
    if (-not $env:SKILLSENTRA_GITHUB_MAX_ATTEMPTS) { $env:SKILLSENTRA_GITHUB_MAX_ATTEMPTS = '2' }
    if (-not $env:SKILLSENTRA_GITHUB_TOTAL_BUDGET_SECONDS) { $env:SKILLSENTRA_GITHUB_TOTAL_BUDGET_SECONDS = '11' }
    & $pythonPath -m app.server --host 127.0.0.1 --port 8766
}
finally {
    Pop-Location
}
