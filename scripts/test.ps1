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
    & $pythonPath -m compileall -q app tests scripts
    if ($LASTEXITCODE -ne 0) { throw 'Python compilation failed.' }
    & $pythonPath -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Automated tests failed.' }
    & $pythonPath scripts/check_docs.py
    if ($LASTEXITCODE -ne 0) { throw 'Documentation link check failed.' }
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    $bundledNode = Join-Path $userProfilePath '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
    $nodePath = if ($env:SKILLSENTRA_NODE -and (Test-Path -LiteralPath $env:SKILLSENTRA_NODE)) {
        $env:SKILLSENTRA_NODE
    }
    elseif ($nodeCommand) {
        $nodeCommand.Source
    }
    else {
        $bundledNode
    }
    if (Test-Path -LiteralPath $nodePath) {
        & $nodePath --check demo-apple/i18n.js
        if ($LASTEXITCODE -ne 0) { throw 'Frontend internationalization syntax check failed.' }
        & $nodePath --check demo-apple/app.js
        if ($LASTEXITCODE -ne 0) { throw 'Frontend JavaScript syntax check failed.' }
        & $nodePath --check demo-apple/ai-truth.js
        if ($LASTEXITCODE -ne 0) { throw 'AI provenance JavaScript syntax check failed.' }
        & $nodePath --check demo-apple/platform.js
        if ($LASTEXITCODE -ne 0) { throw 'Control-plane JavaScript syntax check failed.' }
        & $nodePath --check demo-apple/marketplace.js
        if ($LASTEXITCODE -ne 0) { throw 'Marketplace JavaScript syntax check failed.' }
        & $nodePath --test tests/e2e/ai-truth.contract.test.cjs
        if ($LASTEXITCODE -ne 0) { throw 'AI provenance contract tests failed.' }
    } else {
        throw 'Node.js is required; frontend validation cannot be skipped.'
    }
}
finally {
    Pop-Location
}
